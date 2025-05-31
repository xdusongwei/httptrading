"""
接入盈透证券的API文档
https://ib-insync.readthedocs.io/readme.html
"""
import re
import asyncio
from typing import Any
from httptrading.tool.leaky_bucket import *
from httptrading.tool.time import *
from httptrading.broker.base import *
from httptrading.model import *


@broker_register(name='interactiveBrokers', display='盈透证券', detect_pkg=DetectPkg('ib-insync', 'ib_insync'))
class InteractiveBrokers(SecuritiesBroker):
    def __init__(self, broker_args: dict = None, instance_id: str = None, tokens: list[str] = None):
        super().__init__(broker_args, instance_id, tokens)
        self._lock = asyncio.Lock()
        self._client = None
        self._account_id = None
        self._client_id = None
        self._ib_contracts: dict[Contract, Any] = dict()
        self._plugin_bucket = LeakyBucket(60)
        self._account_bucket = LeakyBucket(60)
        self._order_bucket = LeakyBucket(60)
        self._quote_bucket = LeakyBucket(60)
        self._on_init()

    def _on_init(self):
        self._account_id = self.broker_args.get('account_id')
        self._client_id = self.broker_args.get('client_id')

    async def start(self):
        await self._try_create_client()

    async def shutdown(self):
        ib_socket = self._client
        if ib_socket:
            ib_socket.disconnect()
        self._client = None

    @property
    def timeout(self):
        timeout = max(4, self.broker_args.get('timeout', 8))
        return timeout

    @classmethod
    def ib_contract_to_contract(cls, contract) -> Contract | None:
        if contract.secType != 'STK':
            return None
        symbol = contract.symbol
        trade_type = TradeType.Securities
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
            trade_type=trade_type,
            ticker=ticker,
            region=region,
        )

    async def contract_to_ib_contract(self, contract) -> Any | None:
        import ib_insync
        async with self._lock:
            if contract in self._ib_contracts:
                return self._ib_contracts[contract]
            print(f'{contract}未命中')
            currency = self.contract_to_currency(contract)
            ib_contract = ib_insync.Stock(contract.ticker, 'SMART', currency=currency)
            client = self._client
            client.qualifyContracts(*[ib_contract, ])
            self._ib_contracts[contract] = ib_contract
            return ib_contract

    async def _try_create_client(self):
        import ib_insync
        async with self._lock:
            ib_socket = self._client
            if ib_socket:
                try:
                    ib_dt = await ib_socket.reqCurrentTimeAsync()
                    now = TimeTools.utc_now()
                    if TimeTools.timedelta(ib_dt, seconds=self.timeout) <= now:
                        raise TimeoutError
                    assert ib_socket.isConnected()
                    return
                except Exception as e:
                    pass
            if ib_socket:
                ib_socket.disconnect()
                ib = ib_socket
            else:
                ib = ib_insync.IB()
            host = self.broker_args.get('host', '127.0.0.1')
            port = self.broker_args.get('port', 4000)
            client_id = self.broker_args.get('client_id', self._client_id)
            timeout = self.timeout
            account_id = self._account_id
            new_client = await ib.connectAsync(
                host=host,
                port=port,
                clientId=client_id,
                timeout=timeout,
                account=account_id,
            )
            self._client = new_client

    async def ping(self) -> bool:
        async with self._plugin_bucket:
            try:
                await self._try_create_client()
                return True
            except Exception as e:
                return False

    async def _cash(self) -> Cash:
        import ib_insync
        async with self._account_bucket:
            _client: ib_insync.IB = self._client
            if not _client:
                raise Exception('盈透连接对象未准备好')
            l = await _client.accountSummaryAsync(account=self._account_id)
            d: dict[str, ib_insync.AccountValue] = {i.tag: i for i in l}
            item = d['TotalCashValue']
            assert item.currency == 'USD'
            amount = float(item.value)
            cash = Cash(
                currency='USD',
                amount=amount,
            )
            return cash

    async def cash(self) -> Cash:
        return await self.call_async(self._cash())

    async def _positions(self):
        import ib_insync
        result = list()
        async with self._account_bucket:
            _client: ib_insync.IB = self._client
            if not _client:
                raise Exception('盈透连接对象未准备好')
            l = _client.positions(account=self._account_id)
            for position in l:
                ib_contract = position.contract
                contract = self.ib_contract_to_contract(ib_contract)
                if not contract:
                    continue
                currency = ib_contract.currency
                qty = int(position.position)
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
        return await self.call_async(self._positions())

    async def _place_order(
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
        import ib_insync
        with self._order_bucket:
            client = self._client
            ib_contract = await self.contract_to_ib_contract(contract)

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
                        return ib_insync.LimitOrder(
                            action=direction,
                            totalQuantity=qty,
                            lmtPrice=price,
                        )
                    case OrderType.Market:
                        return ib_insync.MarketOrder(
                            action=direction,
                            totalQuantity=qty,
                        )
                    case _:
                        raise Exception(f'不支持的订单类型: {order_type}')

            ib_order = _map_order()
            ib_order.tif = _map_time_in_force()
            ib_order.outsideRth = _map_lifecycle()
            trade: ib_insync.Trade = client.placeOrder(ib_contract, ib_order)
            await asyncio.sleep(2.0)
            order_id = str(trade.order.permId)
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
        return await self.call_async(self._place_order(
            contract=contract,
            order_type=order_type,
            time_in_force=time_in_force,
            lifecycle=lifecycle,
            direction=direction,
            qty=qty,
            price=price,
            **kwargs
        ))

    async def _cancel_order(self, order_id: str):
        order_id_int = int(order_id)
        with self._order_bucket:
            client = self._client
            trades = client.trades()
            for ib_trade in trades:
                ib_order = ib_trade.order
                if ib_order.permId != order_id_int:
                    continue
                client.cancelOrder(ib_order)
                break

    async def cancel_order(self, order_id: str):
        await self.call_async(self._cancel_order(order_id=order_id))

    async def _order(self, order_id: str) -> Order:
        import ib_insync

        def _total_fills(trade) -> int:
            return int(trade.filled())

        def _avg_price(trade) -> float:
            total_fills = _total_fills(trade)
            if not total_fills:
                return 0
            cap = sum([fill.execution.shares * fill.execution.avgPrice for fill in trade.fills], 0.0)
            return round(cap / total_fills, 5)

        with self._order_bucket:
            client = self._client
            trades = client.trades()
        order_id_int = int(order_id)
        for ib_trade in trades:
            ib_order = ib_trade.order
            if ib_order.permId != order_id_int:
                continue
            qty = int(_total_fills(ib_trade) + ib_trade.remaining())
            filled_qty = _total_fills(ib_trade)
            qty = qty or filled_qty
            assert qty >= filled_qty
            avg_fill_price = _avg_price(ib_trade)
            reason = ''
            if ib_trade.orderStatus.status == ib_insync.OrderStatus.Inactive:
                reason = 'Inactive'

            cancel_status = {ib_insync.OrderStatus.Cancelled, ib_insync.OrderStatus.ApiCancelled, }
            is_cancelled = ib_trade.orderStatus.status in cancel_status
            return Order(
                order_id=order_id,
                currency=ib_trade.contract.currency,
                qty=qty,
                filled_qty=filled_qty,
                avg_price=avg_fill_price,
                error_reason=reason,
                is_canceled=is_cancelled,
            )
        raise Exception(f'查询不到订单{order_id}')

    async def order(self, order_id: str) -> Order:
        return await self.call_async(self._order(order_id))


__all__ = ['InteractiveBrokers', ]
