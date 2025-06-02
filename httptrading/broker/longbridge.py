"""
长桥证券文档见
https://open.longportapp.com/docs
"""
import re
import tomllib
import threading
from decimal import Decimal
from datetime import datetime
import tomlkit
from httptrading.tool.leaky_bucket import *
from httptrading.broker.base import *
from httptrading.model import *
from httptrading.tool.time import *
from httptrading.tool.locate import *


class TokenKeeper:
    """
    长桥证券的会话需要通过下发的 token 凭据验证身份,
    因为 token 有有效期, 该类负责判断 token 是否快要到过期时间(默认剩余3天以内), 使得交易通道对象可以通过 SDK 接口去下载新 token.

    因此, 初次使用的时候, 必须去他们的开发者网站上拿到初代 token, 做成一个 toml 格式文件, TokenKeeper 将读写这个文件.
    这个 toml 文件需要设置两个字段, 分别是 token 和 expiry,
    token 字段填入给的令牌字符串,
    expiry 是过期时间的字符串, 例如 "2025-01-01T00:00:00.000000+00:00"
    没有这个 toml 文件, 或者 token 文件的 expiry 已经过期了, 系统便没办法连接到他们的接口上做自动更新, 因此会有启动上的问题.
    """

    def __init__(self, token_file: str):
        assert token_file
        self.token_file = token_file
        self.lock = threading.RLock()
        with self.lock:
            text = LocateTools.read_file(token_file)
            d = tomllib.loads(text)
            token, expiry = d.get('token'), d.get('expiry')
            assert token
            self.token = token
            self.expiry = datetime.fromisoformat(expiry)
            if self.is_expired:
                raise ValueError(f'长桥证券的访问令牌已经过期{self.expiry}')

    @property
    def is_expired(self):
        if not self.expiry:
            return False
        if TimeTools.utc_now() >= self.expiry:
            return True
        return False

    @property
    def should_refresh(self):
        now = TimeTools.utc_now()
        if now >= self.expiry:
            return False
        if now >= TimeTools.timedelta(self.expiry, days=-3):
            return True
        return False

    def update_token(self, token: str, expiry: datetime):
        assert self.token_file
        assert token
        assert expiry
        with self.lock:
            self.token = token
            self.expiry = expiry
            d = {
                'token': self.token,
                'expiry': self.expiry.isoformat(),
            }
            text = tomlkit.dumps(d)
            LocateTools.write_file(self.token_file, text)


@broker_register(name='longBridge', display='长桥证券', detect_pkg=DetectPkg('longport', 'longport'))
class LongBridge(SecuritiesBroker):
    def __init__(self, broker_args: dict = None, instance_id: str = None, tokens: list[str] = None):
        super().__init__(broker_args, instance_id, tokens)
        self._token_file = ''
        self._token_keeper: TokenKeeper | None = None
        self._auto_refresh_token = False
        self._token_bucket = LeakyBucket(6)
        self._quote_bucket = LeakyBucket(60)
        self._assets_bucket = LeakyBucket(60)
        self._on_init()

    def _on_init(self):
        config_dict = self.broker_args
        token_file = config_dict.get('token_file', '')
        assert token_file
        self._token_file = token_file

        keeper = TokenKeeper(token_file)
        self._token_keeper = keeper

        auto_refresh_token = config_dict.get('auto_refresh_token', False)
        self._auto_refresh_token = auto_refresh_token
        self._reset_client()

    def _reset_client(self):
        cfg = self.broker_args
        from longport.openapi import Config, QuoteContext, TradeContext
        app_key = cfg.get('app_key')
        app_secret = cfg.get('app_secret')
        assert app_key
        assert app_secret
        self._token_keeper = self._token_keeper or TokenKeeper(self._token_file)
        config = Config(
            app_key=app_key,
            app_secret=app_secret,
            access_token=self._token_keeper.token,
        )
        quote_ctx = QuoteContext(config)
        trade_ctx = TradeContext(config)
        self._lp_config = config
        self._quote_client = quote_ctx
        self._trade_client = trade_ctx
        self._when_create_client()

    @classmethod
    def _order_status(cls, lp_order):
        # 订单状态定义见
        # https://open.longportapp.com/zh-CN/docs/trade/trade-definition#orderstatus
        from longport.openapi import OrderStatus, PushOrderChanged, OrderDetail

        canceled_endings = {OrderStatus.Canceled, }
        bad_endings = {
            OrderStatus.Rejected,
            OrderStatus.Expired,
            OrderStatus.PartialWithdrawal,
        }
        pending_cancel_sets = {OrderStatus.PendingCancel, }

        if isinstance(lp_order, PushOrderChanged):
            reason = ''
            if lp_order.status in bad_endings:
                reason = str(lp_order.status)
            is_canceled = lp_order.status in canceled_endings
            is_pending_cancel = lp_order.status in pending_cancel_sets
            return reason, is_canceled, is_pending_cancel
        elif isinstance(lp_order, OrderDetail):
            reason = ''
            if lp_order.status in bad_endings:
                reason = str(lp_order.status)
            is_canceled = lp_order.status in canceled_endings
            is_pending_cancel = lp_order.status in pending_cancel_sets
            return reason, is_canceled, is_pending_cancel
        raise Exception(f'{lp_order}对象不是已知可解析订单状态的类型')

    def _when_create_client(self):
        from longport.openapi import PushOrderChanged, TopicType, OrderStatus

        def _on_order_changed(event: PushOrderChanged):
            reason, is_canceled, is_pending_cancel = self._order_status(event)
            try:
                order = Order(
                    order_id=event.order_id,
                    currency=event.currency,
                    qty=int(event.executed_quantity),
                    filled_qty=int(event.executed_quantity),
                    avg_price=float(event.executed_price) if event.executed_price else 0.0,
                    error_reason=reason,
                    is_canceled=is_canceled,
                    is_pending_cancel=is_pending_cancel,
                )
                self.dump_order(order)
            except Exception as e:
                print(f'[{self.__class__.__name__}]_on_order_changed: {e}\norder: {event}')

        trade_client = self._trade_client
        trade_client.set_on_order_changed(_on_order_changed)
        trade_client.subscribe([TopicType.Private])

    def _try_refresh(self):
        if not self._auto_refresh_token:
            return
        cfg = self._lp_config
        keeper = self._token_keeper
        with keeper.lock:
            if not keeper.should_refresh:
                return
            now = TimeTools.utc_now()
            expiry = TimeTools.timedelta(now, days=90)
            with self._token_bucket:
                token = cfg.refresh_access_token()
                assert token
            keeper.update_token(token, expiry)
            self._reset_client()

    @classmethod
    def symbol_to_contract(cls, symbol: str) -> Contract | None:
        region = ''
        ticker = ''
        if m := re.match(r'^(\S+)\.US$', symbol):
            region = 'US'
            ticker = m.groups()[0]
        if m := re.match(r'^(\d{5})\.HK$', symbol):
            region = 'HK'
            ticker = m.groups()[0]
        if m := re.match(r'^(\d{6})\.SH$', symbol):
            region = 'CN'
            ticker = m.groups()[0]
        if m := re.match(r'^(\d{6})\.SZ$', symbol):
            region = 'CN'
            ticker = m.groups()[0]
        if not region or not ticker:
            return None
        return Contract(
            trade_type=TradeType.Securities,
            ticker=ticker,
            region=region,
        )

    @classmethod
    def contract_to_symbol(cls, contract: Contract) -> str:
        if contract.trade_type != TradeType.Securities:
            raise Exception(f'不能支持的交易品种{contract}映射为交易代码')
        region, ticker = contract.region, contract.ticker
        code = None
        if region == 'CN' and re.match(r'^[56]\d{5}$', ticker):
            code = f'{ticker}.SH'
        elif region == 'CN' and re.match(r'^[013]\d{5}$', ticker):
            code = f'{ticker}.SZ'
        elif region == 'HK' and re.match(r'^\d{5}$', ticker):
            code = f'{ticker}.HK'
        elif region == 'US' and re.match(r'^\w+$', ticker):
            code = f'{ticker}.US'
        if not code:
            raise Exception(f'不能映射{contract}为交易代码')
        return code

    def _positions(self):
        result = list()
        with self._assets_bucket:
            self._try_refresh()
            items = self._trade_client.stock_positions().channels
        for channel in items:
            if channel.account_channel != 'lb':
                continue
            for node in channel.positions:
                symbol = node.symbol
                contract = self.symbol_to_contract(symbol)
                if not contract:
                    continue
                position = Position(
                    broker=self.broker_name,
                    broker_display=self.broker_display,
                    contract=contract,
                    unit=Unit.Share,
                    currency=node.currency,
                    qty=int(node.quantity),
                )
                result.append(position)
        return result

    async def positions(self):
        return await self.call_sync(lambda : self._positions())

    def _cash(self) -> Cash:
        with self._assets_bucket:
            self._try_refresh()
            resp = self._trade_client.account_balance('USD')
        for node in resp:
            if node.currency != 'USD':
                continue
            cash = Cash(
                currency=node.currency,
                amount=float(node.total_cash),
            )
            return cash
        raise Exception('可用资金信息获取不到记录')

    async def cash(self) -> Cash:
        return await self.call_sync(lambda : self._cash())

    def _quote(self, contract: Contract):
        from longport.openapi import TradeStatus
        symbol = self.contract_to_symbol(contract)
        tz = self.contract_to_tz(contract)
        currency = self.contract_to_currency(contract)

        ctx = self._quote_client
        with self._quote_bucket:
            self._try_refresh()
            resp = ctx.quote([symbol, ])
        if not resp:
            raise Exception(f'查询不到{contract}的快照报价')
        item = resp[0]
        update_dt = TimeTools.from_timestamp(item.timestamp.timestamp(), tz)
        is_tradable = bool(item.trade_status == TradeStatus.Normal)
        return Quote(
            contract=contract,
            currency=currency,
            is_tradable=is_tradable,
            latest=float(item.last_done),
            pre_close=float(item.prev_close),
            open_price=float(item.open),
            high_price=float(item.high),
            low_price=float(item.low),
            time=update_dt,
        )

    async def quote(self, contract: Contract):
        return await self.call_sync(lambda : self._quote(contract))

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
        from longport.openapi import OrderType as LbOrderType, OrderSide, TimeInForceType, OutsideRTH
        if contract.trade_type != TradeType.Securities:
            raise Exception(f'不支持的下单品种: {contract.trade_type}')
        if contract.region == 'US' and order_type == OrderType.Market and lifecycle != Lifecycle.RTH:
            raise Exception(f'交易时段不支持市价单')
        symbol = self.contract_to_symbol(contract)
        assert qty > 0
        assert price > 0

        def _map_trade_side():
            match direction:
                case 'BUY':
                    return OrderSide.Buy
                case 'SELL':
                    return OrderSide.Sell
                case _:
                    raise Exception(f'不支持的买卖方向: {direction}')

        def _map_order_type():
            match order_type:
                case OrderType.Market:
                    return LbOrderType.MO
                case OrderType.Limit:
                    return LbOrderType.LO
                case _:
                    raise Exception(f'不支持的订单类型: {order_type}')

        def _map_time_in_force():
            match time_in_force:
                case TimeInForce.DAY:
                    return TimeInForceType.Day
                case TimeInForce.GTC:
                    return TimeInForceType.GoodTilCanceled
                case _:
                    raise Exception(f'不支持的订单有效期: {time_in_force}')

        def _map_lifecycle():
            match lifecycle:
                case Lifecycle.RTH:
                    return OutsideRTH.RTHOnly
                case Lifecycle.ETH:
                    return OutsideRTH.AnyTime # 没错, 允许盘前盘后
                case Lifecycle.OVERNIGHT:
                    return OutsideRTH.Overnight
                case _:
                    raise Exception(f'不支持的交易时段: {lifecycle}')

        with self._assets_bucket:
            self._try_refresh()
            resp = self._trade_client.submit_order(
                symbol=symbol,
                order_type=_map_order_type(),
                side=_map_trade_side(),
                outside_rth=_map_lifecycle(),
                submitted_quantity=Decimal(qty),
                time_in_force=_map_time_in_force(),
                submitted_price=Decimal(price) if price is not None else None,
            )
        assert resp.order_id
        return resp.order_id

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
        return await self.call_sync(lambda : self._place_order(
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
        with self._assets_bucket:
            self._try_refresh()
            resp = self._trade_client.order_detail(order_id=order_id)
        reason, is_canceled, is_pending_cancel = self._order_status(resp)
        return Order(
            order_id=order_id,
            currency=resp.currency,
            qty=int(resp.quantity),
            filled_qty=int(resp.executed_quantity),
            avg_price=float(resp.executed_price) if resp.executed_price else 0.0,
            error_reason=reason,
            is_canceled=is_canceled,
            is_pending_cancel=is_pending_cancel,
        )

    async def order(self, order_id: str) -> Order:
        return await self.call_sync(lambda : self._order(order_id=order_id))

    def _cancel_order(self, order_id: str):
        with self._assets_bucket:
            self._try_refresh()
            self._trade_client.cancel_order(order_id=order_id)

    async def cancel_order(self, order_id: str):
        await self.call_sync(lambda: self._cancel_order(order_id=order_id))


__all__ = ['LongBridge', ]
