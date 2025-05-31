"""
接入老虎证券的API文档
https://quant.itigerup.com/openapi/zh/python/overview/introduction.html
"""
import re
import threading
from httptrading.tool.leaky_bucket import *
from httptrading.broker.base import *
from httptrading.model import *


@broker_register(name='tiger', display='老虎证券', detect_pkg=DetectPkg('tigeropen', 'tigeropen'))
class Tiger(SecuritiesBroker):
    def __init__(self, broker_args: dict = None, instance_id: str = None, tokens: list[str] = None):
        super().__init__(broker_args, instance_id, tokens)
        self._has_grab = False
        self._config = None
        self._quote_client = None
        self._trade_client = None
        self._push_client = None
        self._grab_lock = threading.Lock()
        self._market_status_bucket = LeakyBucket(9)
        self._quote_bucket = LeakyBucket(119)
        self._order_bucket = LeakyBucket(119)
        self._assets_bucket = LeakyBucket(59)
        self._on_init()

    def _on_init(self):
        from tigeropen.tiger_open_config import get_client_config
        from tigeropen.quote.quote_client import QuoteClient
        from tigeropen.trade.trade_client import TradeClient
        from tigeropen.push.push_client import PushClient
        config_dict = self.broker_args
        pk_path = config_dict.get('pk_path')
        tiger_id = config_dict.get('tiger_id')
        account = config_dict.get('account')
        timeout = config_dict.get('timeout', 8)
        client_config = get_client_config(
            private_key_path=pk_path,
            tiger_id=tiger_id,
            account=account,
            timeout=timeout,
        )
        self._config = client_config
        self._quote_client = self._quote_client or QuoteClient(client_config, is_grab_permission=False)
        self._trade_client = self._trade_client or TradeClient(client_config)
        protocol, host, port = client_config.socket_host_port
        self._push_client = self._push_client or PushClient(host, port, use_ssl=(protocol == 'ssl'))

    def _grab_quote(self):
        with self._grab_lock:
            if self._has_grab:
                return
            try:
                config_dict = self.broker_args
                if mac_address := config_dict.get('mac_address', None):
                    from tigeropen.tiger_open_config import TigerOpenClientConfig
                    TigerOpenClientConfig.__get_device_id = lambda: mac_address
            except Exception as e:
                pass
            self._quote_client.grab_quote_permission()
            self._has_grab = True

    @classmethod
    def symbol_to_contract(cls, symbol) -> Contract | None:
        region = ''
        ticker = ''
        if re.match(r'^[01356]\d{5}$', symbol):
            region = 'CN'
            ticker = symbol
        if re.match(r'^\d{5}$', symbol):
            region = 'HK'
            ticker = symbol
        if re.match(r'^\w{1,5}$', symbol):
            region = 'US'
            ticker = symbol
        if not region or not ticker:
            return None
        return Contract(
            trade_type=TradeType.Securities,
            ticker=ticker,
            region=region,
        )

    @classmethod
    def contract_to_symbol(cls, contract: Contract) -> str | None:
        if contract.trade_type != TradeType.Securities:
            return None
        region, ticker = contract.region, contract.ticker
        symbol = None
        if region == 'CN' and re.match(r'^[01356]\d{5}$', ticker):
            symbol = ticker
        elif region == 'HK' and re.match(r'^\d{5}$', ticker):
            symbol = ticker
        elif region == 'US' and re.match(r'^\w{1,5}$', ticker):
            symbol = ticker
        return symbol

    def _positions(self):
        from tigeropen.trade.domain.position import Position as TigerPosition
        result = list()
        with self._assets_bucket:
            positions: list[TigerPosition] = self._trade_client.get_positions()
        for p in positions or list():
            symbol = p.contract.symbol
            currency = p.contract.currency

            if not symbol or not currency:
                continue
            contract = self.symbol_to_contract(symbol)
            if not contract:
                continue
            qty = int(p.quantity)
            position = Position(
                broker=self.broker_name,
                broker_display=self.broker_display,
                contract=contract,
                unit=Unit.Share,
                currency=currency,
                qty=qty,
            )
            result.append(position)
        return result

    async def positions(self):
        return await self.call_sync(lambda: self._positions())

    def _cash(self) -> Cash:
        from tigeropen.trade.domain.prime_account import PortfolioAccount, Segment
        with self._assets_bucket:
            portfolio_account: PortfolioAccount = self._trade_client.get_prime_assets()
        s: Segment = portfolio_account.segments.get('S')
        currency = s.currency
        assert currency == 'USD'
        cash_balance = s.cash_balance
        cash = Cash(
            currency='USD',
            amount=cash_balance,
        )
        return cash

    async def cash(self) -> Cash:
        return await self.call_sync(lambda: self._cash())

    def _market_status(self) -> dict[str, dict[str, MarketStatus]]:
        from tigeropen.common.consts import Market
        from tigeropen.quote.domain.market_status import MarketStatus as TigerMarketStatus
        client = self._quote_client
        with self._market_status_bucket:
            ms_list: list[TigerMarketStatus] = client.get_market_status(Market.ALL)

        # 各个市场的状态定义见:
        # https://quant.itigerup.com/openapi/zh/python/operation/quotation/stock.html#get-market-status-%E8%8E%B7%E5%8F%96%E5%B8%82%E5%9C%BA%E7%8A%B6%E6%80%81
        sec_result = dict()
        status_map = {
            'CLOSING': UnifiedStatus.CLOSED,
            'EARLY_CLOSED': UnifiedStatus.CLOSED,
            'MARKET_CLOSED': UnifiedStatus.CLOSED,
            'PRE_HOUR_TRADING': UnifiedStatus.PRE_HOURS,
            'TRADING': UnifiedStatus.RTH,
            'POST_HOUR_TRADING': UnifiedStatus.AFTER_HOURS,
            # 这些映射是A股港股市场的映射
            'MIDDLE_CLOSE': UnifiedStatus.REST,
        }
        for ms in ms_list:
            region = ms.market
            origin_status = ms.trading_status
            unified_status = status_map.get(origin_status, UnifiedStatus.UNKNOWN)
            sec_result[region] = MarketStatus(
                region=region,
                origin_status=origin_status,
                unified_status=unified_status,
            )
        return {
            TradeType.Securities.name.lower(): sec_result,
        }

    async def market_status(self) -> dict[str, dict[str, MarketStatus]]:
        return await self.call_sync(lambda: self._market_status())

    def _quote(self, contract: Contract):
        import pandas
        self._grab_quote()
        symbol = self.contract_to_symbol(contract)
        currency = self.contract_to_currency(contract)
        with self._quote_bucket:
            pd = self._quote_client.get_stock_briefs(symbols=[symbol, ])
        pd['us_date'] = pandas \
            .to_datetime(pd['latest_time'], unit='ms') \
            .dt.tz_localize('UTC') \
            .dt.tz_convert('US/Eastern')
        for index, row in pd.iterrows():
            us_date, pre_close, open_price, latest_price, status, low_price, high_price = (
                row['us_date'],
                row['pre_close'],
                row['open'],
                row['latest_price'],
                row['status'],
                row['low'],
                row['high'],
            )
            is_tradable = status == 'NORMAL'
            return Quote(
                contract=contract,
                currency=currency,
                is_tradable=is_tradable,
                latest=latest_price,
                pre_close=pre_close,
                open_price=open_price,
                high_price=high_price,
                low_price=low_price,
                time=us_date,
            )
        raise Exception(f'没有找到{contract}的快照行情')

    async def quote(self, contract: Contract):
        return await self.call_sync(lambda: self._quote(contract))

    def _place_order(
            self,
            contract: Contract,
            order_type: OrderType,
            time_in_force: TimeInForce,
            lifecycle: Lifecycle,
            direction: str,
            qty: int,
            price: float = None,
            **kwargs
    ) -> str:
        if contract.trade_type != TradeType.Securities:
            raise Exception(f'不支持的下单品种: {contract.trade_type}')
        if contract.region == 'US' and order_type == OrderType.Market and lifecycle != Lifecycle.RTH:
            raise Exception(f'交易时段不支持市价单')
        symbol = self.contract_to_symbol(contract)
        currency = self.contract_to_currency(contract)
        from tigeropen.common.util.contract_utils import stock_contract
        from tigeropen.tiger_open_config import TigerOpenClientConfig
        from tigeropen.common.util.order_utils import market_order, limit_order
        cfg: TigerOpenClientConfig = self._config
        client = self._trade_client
        tiger_contract = stock_contract(symbol=symbol, currency=currency)

        def _map_trade_side():
            match direction:
                case 'BUY':
                    return direction
                case 'SELL':
                    return direction
                case _:
                    raise Exception(f'不支持的买卖方向: {direction}')

        def _map_time_in_force():
            match time_in_force:
                case TimeInForce.DAY:
                    return 'DAY'
                case TimeInForce.GTC:
                    return 'GTC'
                case _:
                    raise Exception(f'不支持的订单有效期: {time_in_force}')

        def _map_lifecycle():
            match lifecycle:
                case Lifecycle.RTH:
                    return False
                case Lifecycle.ETH:
                    return True
                case _:
                    raise Exception(f'不支持的交易时段: {lifecycle}')

        def _map_order():
            match order_type:
                case OrderType.Limit:
                    return limit_order(
                        account=cfg.account,
                        contract=tiger_contract,
                        action=_map_trade_side(),
                        quantity=qty,
                        limit_price=price,
                    )
                case OrderType.Market:
                    return market_order(
                        account=cfg.account,
                        contract=tiger_contract,
                        action=_map_trade_side(),
                        quantity=qty,
                    )
                case _:
                    raise Exception(f'不支持的订单类型: {order_type}')
        tiger_order = _map_order()
        tiger_order.time_in_force = time_in_force=_map_time_in_force()
        tiger_order.outside_rth = _map_lifecycle()
        with self._order_bucket:
            client.place_order(tiger_order)
        order_id = str(tiger_order.id)
        assert order_id
        return order_id

    async def place_order(
            self,
            contract: Contract,
            order_type: OrderType,
            time_in_force: TimeInForce,
            lifecycle: Lifecycle,
            direction: str,
            qty: int,
            price: float = None,
            **kwargs
    ) -> str:
        return await self.call_sync(lambda: self._place_order(
            contract=contract,
            order_type=order_type,
            time_in_force=time_in_force,
            lifecycle=lifecycle,
            direction=direction,
            qty=qty,
            price=price,
            **kwargs
        ))

    def _order(self, order_id: str) -> Order:
        from tigeropen.trade.domain.order import Order as TigerOrder, OrderStatus
        with self._order_bucket:
            tiger_order: TigerOrder = self._trade_client.get_order(id=int(order_id))
        if tiger_order is None:
            raise Exception(f'查询不到订单{order_id}')
        return Order(
            order_id=order_id,
            currency=tiger_order.contract.currency,
            qty=tiger_order.quantity or 0,
            filled_qty=tiger_order.filled or 0,
            avg_price=tiger_order.avg_fill_price or 0.0,
            error_reason=tiger_order.reason,
            is_canceled=tiger_order.status == OrderStatus.CANCELLED,
        )

    async def order(self, order_id: str) -> Order:
        return await self.call_sync(lambda: self._order(order_id=order_id))

    def _cancel_order(self, order_id: str):
        with self._order_bucket:
            self._trade_client.cancel_order(id=int(order_id))

    async def cancel_order(self, order_id: str):
        await self.call_sync(lambda: self._cancel_order(order_id=order_id))


__all__ = ['Tiger', ]
