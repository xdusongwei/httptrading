"""
接入富途证券的API文档
https://openapi.futunn.com/futu-api-doc/
"""
import re
from datetime import datetime
from httptrading.tool.leaky_bucket import *
from httptrading.broker.base import *
from httptrading.model import *
from httptrading.tool.time import *


@broker_register(name='futu', display='富途证券', detect_pkg=DetectPkg('futu-api', 'futu'))
class Futu(SecuritiesBroker):
    def __init__(self, broker_args: dict = None, instance_id: str = None, tokens: list[str] = None):
        super().__init__(broker_args, instance_id, tokens)
        self._unlock_pin = ''
        self._trd_env = 'REAL'
        self._quote_client = None
        self._trade_client = None
        self._market_status_bucket = LeakyBucket(10)
        self._snapshot_bucket = LeakyBucket(120)
        self._assets_bucket = LeakyBucket(20)
        self._position_bucket = LeakyBucket(20)
        self._unlock_bucket = LeakyBucket(20)
        self._place_order_bucket = LeakyBucket(30)
        self._cancel_order_bucket = LeakyBucket(30)
        self._refresh_order_bucket = LeakyBucket(20)
        self._on_init()

    def _on_init(self):
        from futu import SysConfig, OpenQuoteContext, OpenSecTradeContext, SecurityFirm, TrdMarket, TrdEnv

        config_dict = self.broker_args
        self._trd_env: str = config_dict.get('trade_env', TrdEnv.REAL) or TrdEnv.REAL
        pk_path = config_dict.get('pk_path', '')
        self._unlock_pin = config_dict.get('unlock_pin', '')
        if pk_path:
            SysConfig.enable_proto_encrypt(is_encrypt=True)
            SysConfig.set_init_rsa_file(pk_path)
        if self._trade_client is None:
            SysConfig.set_all_thread_daemon(True)
            host = config_dict.get('host', '127.0.0.1')
            port = config_dict.get('port', 11111)
            trade_ctx = OpenSecTradeContext(
                filter_trdmarket=TrdMarket.US,
                host=host,
                port=port,
                security_firm=SecurityFirm.FUTUSECURITIES,
            )
            trade_ctx.set_sync_query_connect_timeout(6.0)
            self._trade_client = trade_ctx
            self._when_create_client()
        if self._quote_client is None:
            SysConfig.set_all_thread_daemon(True)
            host = config_dict.get('host', '127.0.0.1')
            port = config_dict.get('port', 11111)
            quote_ctx = OpenQuoteContext(host=host, port=port)
            quote_ctx.set_sync_query_connect_timeout(6.0)
            self._quote_client = quote_ctx

    def _when_create_client(self):
        from futu import TradeOrderHandlerBase, RET_OK, OpenSecTradeContext

        client: OpenSecTradeContext = self._trade_client

        def _on_recv_rsp(content):
            for _futu_order in self._df_to_list(content):
                try:
                    _order = self._build_order(_futu_order)
                    self.dump_order(_order)
                except Exception as _ex:
                    print(f'[{self.__class__.__name__}]_on_recv_rsp: {_ex}\norder: {_futu_order}')

        class TradeOrderHandler(TradeOrderHandlerBase):
            def on_recv_rsp(self, rsp_pb):
                ret, content = super().on_recv_rsp(rsp_pb)
                if ret == RET_OK:
                    _on_recv_rsp(content)
                return ret, content

        client.set_handler(TradeOrderHandler())

    async def start(self):
        from futu import RET_OK, OpenSecTradeContext

        client: OpenSecTradeContext = self._trade_client
        if HtGlobalConfig.DUMP_ACTIVE_ORDERS:
            try:
                ret, data = client.order_list_query(
                    refresh_cache=True,
                    trd_env=self._trd_env,
                )
            except Exception as e:
                print(f'[{self.__class__.__name__}]DUMP_ACTIVE_ORDERS: {e}')
            else:
                if ret == RET_OK:
                    futu_orders = self._df_to_list(data)
                    for futu_order in futu_orders:
                        try:
                            order = self._build_order(futu_order)
                            await self.call_sync(lambda : self.dump_order(order))
                        except Exception as ex:
                            print(f'[{self.__class__.__name__}]DUMP_ACTIVE_ORDERS: {ex}\norder: {futu_order}')

    @classmethod
    def code_to_contract(cls, code) -> Contract | None:
        region = ''
        ticker = ''
        if m := re.match(r'^US\.(\S+)$', code):
            region = 'US'
            ticker = m.groups()[0]
        if m := re.match(r'^HK\.(\d{5})$', code):
            region = 'HK'
            ticker = m.groups()[0]
        if m := re.match(r'^SH\.(\d{6})$', code):
            region = 'CN'
            ticker = m.groups()[0]
        if m := re.match(r'^SZ\.(\d{6})$', code):
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
    def contract_to_code(cls, contract: Contract) -> str | None:
        if contract.trade_type != TradeType.Securities:
            return None
        region, ticker = contract.region, contract.ticker
        code = None
        if region == 'CN' and re.match(r'^[56]\d{5}$', ticker):
            code = f'SH.{ticker}'
        elif region == 'CN' and re.match(r'^[013]\d{5}$', ticker):
            code = f'SZ.{ticker}'
        elif region == 'HK' and re.match(r'^\d{5}$', ticker):
            code = f'HK.{ticker}'
        elif region == 'US' and re.match(r'^\w+$', ticker):
            code = f'US.{ticker}'
        return code

    @classmethod
    def _df_to_list(cls, df) -> list[dict]:
        return df.to_dict(orient='records')

    def _positions(self):
        result = list()
        from futu import RET_OK
        with self._position_bucket:
            resp, data = self._trade_client.position_list_query(
                trd_env=self._trd_env,
                refresh_cache=True,
            )
        if resp != RET_OK:
            raise Exception(f'返回失败: {resp}')
        positions = self._df_to_list(data)
        for d in positions:
            code = d.get('code')
            currency = d.get('currency')
            if not code or not currency:
                continue
            contract = self.code_to_contract(code)
            if not contract:
                continue
            qty = int(d['qty'])
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
        return await self.call_sync(lambda : self._positions())

    async def ping(self) -> bool:
        try:
            client = self._trade_client
            conn_id = client.get_sync_conn_id()
            return bool(conn_id)
        except Exception as e:
            return False

    def _cash(self) -> Cash:
        from futu import RET_OK, Currency
        with self._assets_bucket:
            resp, data = self._trade_client.accinfo_query(
                trd_env=self._trd_env,
                refresh_cache=True,
                currency=Currency.USD,
            )
        if resp != RET_OK:
            raise Exception(f'可用资金信息获取失败: {data}')
        assets = self._df_to_list(data)
        if len(assets) == 1:
            cash = Cash(
                currency='USD',
                amount=assets[0]['cash'],
            )
            return cash
        else:
            raise Exception(f'可用资金信息获取不到记录')

    async def cash(self) -> Cash:
        return await self.call_sync(lambda : self._cash())

    def _market_status(self) -> dict[TradeType, dict[str, MarketStatus] | str]:
        # 各个市场的状态定义见:
        # https://openapi.futunn.com/futu-api-doc/qa/quote.html#2090
        from futu import RET_OK
        sec_result = dict()
        region_map = {
            'market_sh': 'CN',
            'market_hk': 'HK',
            'market_us': 'US',
        }
        status_map = {
            'CLOSED': UnifiedStatus.CLOSED,
            'PRE_MARKET_BEGIN': UnifiedStatus.PRE_HOURS,
            'MORNING': UnifiedStatus.RTH,
            'AFTERNOON': UnifiedStatus.RTH,
            'AFTER_HOURS_BEGIN': UnifiedStatus.AFTER_HOURS,
            'AFTER_HOURS_END': UnifiedStatus.CLOSED, # 根据文档, 盘后收盘时段跟夜盘时段重合
            'OVERNIGHT': UnifiedStatus.OVERNIGHT,
            # 这些映射是A股港股市场的映射
            'REST': UnifiedStatus.REST,
            'HK_CAS': UnifiedStatus.CLOSED,
        }
        client = self._quote_client
        with self._market_status_bucket:
            ret, data = client.get_global_state()
        if ret != RET_OK:
            raise Exception(f'市场状态接口调用失败: {data}')
        for k, origin_status in data.items():
            if k not in region_map:
                continue
            region = region_map[k]
            unified_status = status_map.get(origin_status, UnifiedStatus.UNKNOWN)
            sec_result[region] = MarketStatus(
                region=region,
                origin_status=origin_status,
                unified_status=unified_status,
            )
        return {
            TradeType.Securities: sec_result,
        }

    async def market_status(self) -> dict[TradeType, dict[str, MarketStatus] | str]:
        return await self.call_sync(lambda : self._market_status())

    def _quote(self, contract: Contract):
        from futu import RET_OK
        tz = self.contract_to_tz(contract)
        code = self.contract_to_code(contract)
        currency = self.contract_to_currency(contract)
        with self._snapshot_bucket:
            ret, data = self._quote_client.get_market_snapshot([code, ])
        if ret != RET_OK:
            raise ValueError(f'快照接口调用失败: {data}')
        table = self._df_to_list(data)
        if len(table) != 1:
            raise ValueError(f'快照接口调用无数据: {data}')
        d = table[0]
        """
        格式：yyyy-MM-dd HH:mm:ss
        港股和 A 股市场默认是北京时间，美股市场默认是美东时间
        """
        update_time: str = d['update_time']
        update_dt = datetime.strptime(update_time, '%Y-%m-%d %H:%M:%S')
        update_dt = TimeTools.from_params(
            year=update_dt.year,
            month=update_dt.month,
            day=update_dt.day,
            hour=update_dt.hour,
            minute=update_dt.minute,
            second=update_dt.second,
            tz=tz,
        )
        is_tradable = d['sec_status'] == 'NORMAL'
        return Quote(
            contract=contract,
            currency=currency,
            is_tradable=is_tradable,
            latest=d['last_price'],
            pre_close=d['prev_close_price'],
            open_price=d['open_price'],
            high_price=d['high_price'],
            low_price=d['low_price'],
            time=update_dt,
        )

    async def quote(self, contract: Contract):
        return await self.call_sync(lambda : self._quote(contract))

    def _try_unlock(self):
        from futu import RET_OK, TrdEnv
        if self._trd_env != TrdEnv.REAL:
            return
        if not self._unlock_pin:
            return
        with self._unlock_bucket:
            ret, data = self._trade_client.unlock_trade(password_md5=self._unlock_pin)
        if ret != RET_OK:
            raise Exception(f'解锁交易失败: {data}')

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
        from futu import RET_OK, TrdSide, OrderType as FutuOrderType, TimeInForce as FutuTimeInForce, Session
        if contract.trade_type != TradeType.Securities:
            raise Exception(f'不支持的下单品种: {contract.trade_type}')
        if contract.region == 'US' and order_type == OrderType.Market and lifecycle != Lifecycle.RTH:
            raise Exception(f'交易时段不支持市价单')
        code = self.contract_to_code(contract)
        assert qty > 0
        assert price > 0

        def _map_trade_side():
            match direction:
                case 'BUY':
                    return TrdSide.BUY
                case 'SELL':
                    return TrdSide.SELL
                case _:
                    raise Exception(f'不支持的买卖方向: {direction}')

        def _map_order_type():
            match order_type:
                case OrderType.Market:
                    return FutuOrderType.MARKET
                case OrderType.Limit:
                    return FutuOrderType.NORMAL
                case _:
                    raise Exception(f'不支持的订单类型: {order_type}')

        def _map_time_in_force():
            match time_in_force:
                case TimeInForce.DAY:
                    return FutuTimeInForce.DAY
                case TimeInForce.GTC:
                    return FutuTimeInForce.GTC
                case _:
                    raise Exception(f'不支持的订单有效期: {time_in_force}')

        def _map_lifecycle():
            match lifecycle:
                case Lifecycle.RTH:
                    return Session.RTH
                case Lifecycle.ETH:
                    return Session.ETH
                case Lifecycle.OVERNIGHT:
                    return Session.OVERNIGHT
                case _:
                    raise Exception(f'不支持的交易时段: {lifecycle}')

        self._try_unlock()
        with self._place_order_bucket:
            ret, data = self._trade_client.place_order(
                code=code,
                price=price or 10.0, # 富途必须要填充这个字段
                qty=qty,
                trd_side=_map_trade_side(),
                order_type=_map_order_type(),
                time_in_force=_map_time_in_force(),
                trd_env=self._trd_env,
                session=_map_lifecycle(),
            )
        if ret != RET_OK:
            raise Exception(f'下单失败: {data}')
        orders = self._df_to_list(data)
        assert len(orders) == 1
        order_id = orders[0]['order_id']
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

    @classmethod
    def _build_order(cls, futu_order: dict) -> Order:
        from futu import OrderStatus
        """
        富途证券的状态定义
        NONE = "N/A"                                # 未知状态
        UNSUBMITTED = "UNSUBMITTED"                 # 未提交
        WAITING_SUBMIT = "WAITING_SUBMIT"           # 等待提交
        SUBMITTING = "SUBMITTING"                   # 提交中
        SUBMIT_FAILED = "SUBMIT_FAILED"             # 提交失败，下单失败
        TIMEOUT = "TIMEOUT"                         # 处理超时，结果未知
        SUBMITTED = "SUBMITTED"                     # 已提交，等待成交
        FILLED_PART = "FILLED_PART"                 # 部分成交
        FILLED_ALL = "FILLED_ALL"                   # 全部已成
        CANCELLING_PART = "CANCELLING_PART"         # 正在撤单_部分(部分已成交，正在撤销剩余部分)
        CANCELLING_ALL = "CANCELLING_ALL"           # 正在撤单_全部
        CANCELLED_PART = "CANCELLED_PART"           # 部分成交，剩余部分已撤单
        CANCELLED_ALL = "CANCELLED_ALL"             # 全部已撤单，无成交
        FAILED = "FAILED"                           # 下单失败，服务拒绝
        DISABLED = "DISABLED"                       # 已失效
        DELETED = "DELETED"                         # 已删除，无成交的订单才能删除
        FILL_CANCELLED = "FILL_CANCELLED"           # 成交被撤销，一般遇不到，意思是已经成交的订单被回滚撤销，成交无效变为废单
        """
        canceled_endings = {OrderStatus.CANCELLED_ALL, OrderStatus.CANCELLED_PART, }
        bad_endings = {
            OrderStatus.SUBMIT_FAILED,
            OrderStatus.FAILED,
            OrderStatus.DISABLED,
            OrderStatus.DELETED,
            OrderStatus.FILL_CANCELLED,  # 不清楚对于成交数量有何影响.
        }
        pending_cancel_sets = {OrderStatus.CANCELLING_PART, OrderStatus.CANCELLING_ALL, }

        order_id = futu_order['order_id']
        reason = ''
        order_status: str = futu_order['order_status']
        if order_status in bad_endings:
            reason = order_status
        is_canceled = order_status in canceled_endings
        is_pending_cancel = order_status in pending_cancel_sets
        return Order(
            order_id=order_id,
            currency=futu_order['currency'],
            qty=int(futu_order['qty']),
            filled_qty=int(futu_order['dealt_qty']),
            avg_price=futu_order['dealt_avg_price'] or 0.0,
            error_reason=reason,
            is_canceled=is_canceled,
            is_pending_cancel=is_pending_cancel,
        )

    def _order(self, order_id: str) -> Order:
        from futu import RET_OK

        with self._refresh_order_bucket:
            ret, data = self._trade_client.order_list_query(
                order_id=order_id,
                refresh_cache=True,
                trd_env=self._trd_env,
            )
        if ret != RET_OK:
            raise Exception(f'调用获取订单失败, 订单: {order_id}')
        orders = self._df_to_list(data)
        if len(orders) != 1:
            raise Exception(f'找不到订单(未完成), 订单: {order_id}')
        futu_order = orders[0]
        return self._build_order(futu_order)

    async def order(self, order_id: str) -> Order:
        return await self.call_sync(lambda : self._order(order_id=order_id))

    def _cancel_order(self, order_id: str):
        from futu import RET_OK, ModifyOrderOp
        self._try_unlock()
        with self._cancel_order_bucket:
            ret, data = self._trade_client.modify_order(
                modify_order_op=ModifyOrderOp.CANCEL,
                order_id=order_id,
                qty=100,
                price=10.0,
                trd_env=self._trd_env,
            )
        if ret != RET_OK:
            raise Exception(f'撤单失败, 订单: {order_id}, 原因: {data}')

    async def cancel_order(self, order_id: str):
        await self.call_sync(lambda: self._cancel_order(order_id=order_id))


__all__ = ['Futu', ]
