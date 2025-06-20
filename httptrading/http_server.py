import json
import asyncio
from datetime import datetime, UTC
from typing import Callable
from aiohttp import web
from httptrading.broker.base import *
from httptrading.model import *


class HttpTradingView(web.View):
    __BROKERS: list[BaseBroker] = list()

    @classmethod
    def set_brokers(cls, brokers: list[BaseBroker]):
        HttpTradingView.__BROKERS = brokers

    @classmethod
    def brokers(cls):
        return HttpTradingView.__BROKERS.copy()

    def instance_id(self) -> str:
        return self.request.match_info.get('instance_id', '')

    def current_broker(self) -> BaseBroker:
        broker = getattr(self.request, '__current_broker__', None)
        if broker is None:
            raise web.HTTPNotFound()
        return broker

    async def get_contract(self, from_json=False):
        params: dict = await self.request.json() if from_json else self.request.query
        trade_type = TradeType[params.get('tradeType', '--')]
        region = params.get('region', '--')
        symbol = params.get('symbol', '--')
        contract = Contract(
            trade_type=trade_type,
            region=region,
            symbol=symbol,
        )
        return contract

    @classmethod
    def dumps(cls, obj):
        return json.dumps(obj, default=HtGlobalConfig.JSON_DEFAULT.json_default)

    @classmethod
    def response_obj(cls, obj):
        return web.Response(text=cls.dumps(obj), content_type='application/json')

    @classmethod
    def response_api(cls, broker: BaseBroker = None, args: dict = None, ex: Exception = None):
        resp = {
            'type': 'apiResponse',
            'instanceId': broker.instance_id if broker else None,
            'broker': broker.broker_name if broker else None,
            'brokerDisplay': broker.broker_display if broker else None,
            'time': datetime.now(UTC).isoformat(),
            'ex': ex.__str__() if ex else None,
        }
        if args:
            resp.update(args)
        return cls.response_obj(resp)


class PlaceOrderView(HttpTradingView):
    async def post(self):
        broker = self.current_broker()
        contract = await self.get_contract(from_json=True)
        body_d: dict = await self.request.json()
        price = body_d.get('price', 0)
        qty = body_d.get('qty', 0)
        order_type = OrderType[body_d.get('orderType', '')]
        time_in_force = TimeInForce[body_d.get('timeInForce', '')]
        lifecycle = Lifecycle[body_d.get('lifecycle', '')]
        direction = body_d.get('direction', '')
        if price:
            price = float(price)
        order_id: str = await broker.place_order(
            contract=contract,
            order_type=order_type,
            time_in_force=time_in_force,
            lifecycle=lifecycle,
            direction=direction,
            qty=qty,
            price=price,
            json=body_d,
            full_args=body_d,
        )
        return self.response_api(broker, {
            'orderId': order_id,
            'args': body_d,
        })


class OrderStateView(HttpTradingView):
    async def get(self):
        broker = self.current_broker()
        order_id = self.request.query.get('orderId', '')
        order: Order = await broker.order(order_id=order_id)
        _ = asyncio.create_task(broker.call_sync(lambda : broker.dump_order(order)))
        return self.response_api(broker, {
            'order': order,
        })


class CancelOrderView(HttpTradingView):
    async def post(self):
        broker = self.current_broker()
        body_d: dict = await self.request.json()
        order_id = body_d.get('orderId', '')
        assert order_id
        await broker.cancel_order(order_id=order_id)
        return self.response_api(broker, {
            'canceled': True,
        })


class CashView(HttpTradingView):
    async def get(self):
        broker = self.current_broker()
        cash: Cash = await broker.cash()
        return self.response_api(broker, {
            'cash': cash,
        })


class PositionView(HttpTradingView):
    async def get(self):
        broker = self.current_broker()
        positions: list[Position] = await broker.positions()
        return self.response_api(broker, {
            'positions': positions,
        })


class PlugInView(HttpTradingView):
    async def get(self):
        broker = self.current_broker()
        pong = await broker.ping()
        return self.response_api(broker, {
            'pong': pong,
        })


class QuoteView(HttpTradingView):
    async def get(self):
        contract = await self.get_contract()
        broker = self.current_broker()
        quote: Quote = await broker.quote(contract)
        return self.response_api(broker, {
            'quote': quote,
        })


class MarketStatusView(HttpTradingView):
    async def get(self):
        broker = self.current_broker()
        ms_dict = await broker.market_status()
        ms_dict = {t.name.lower(): d for t, d in ms_dict.items()}
        ms_dict['type'] = 'marketStatusMap'
        return self.response_api(broker, {
            'marketStatus': ms_dict,
        })


def create_auth_middleware(token_header: str):
    assert isinstance(token_header, str)
    assert token_header

    @web.middleware
    async def _auth_middleware(request: web.Request, handler):
        instance_id = request.match_info.get('instance_id', '')
        token = request.headers.get(token_header, '')
        if not instance_id:
            raise web.HTTPNotFound
        if not token:
            raise web.HTTPNotFound
        if len(token) < 16 or len(token) > 64:
            raise web.HTTPNotFound
        for broker in HttpTradingView.brokers():
            if broker.instance_id != instance_id:
                continue
            if token not in broker.tokens:
                raise web.HTTPNotFound
            setattr(request, '__current_broker__', broker)
            break
        else:
            raise web.HTTPNotFound
        response: web.Response = await handler(request)
        delattr(request, '__current_broker__')
        return response
    return _auth_middleware


@web.middleware
async def exception_middleware(request: web.Request, handler):
    try:
        response: web.Response = await handler(request)
        return response
    except BrokerError as ex:
        return HttpTradingView.response_api(broker=ex.broker, ex=ex)
    except Exception as ex:
        broker = getattr(request, '__current_broker__', None)
        return HttpTradingView.response_api(broker=broker, ex=ex)


def std_api_factory() -> list[web.RouteDef]:
    apis = [
        web.view(r'/httptrading/api/{instance_id:\w{16,32}}/order/place', PlaceOrderView),
        web.view(r'/httptrading/api/{instance_id:\w{16,32}}/order/state', OrderStateView),
        web.view(r'/httptrading/api/{instance_id:\w{16,32}}/order/cancel', CancelOrderView),
        web.view(r'/httptrading/api/{instance_id:\w{16,32}}/cash/state', CashView),
        web.view(r'/httptrading/api/{instance_id:\w{16,32}}/position/state', PositionView),
        web.view(r'/httptrading/api/{instance_id:\w{16,32}}/ping/state', PlugInView),
        web.view(r'/httptrading/api/{instance_id:\w{16,32}}/market/state', MarketStatusView),
        web.view(r'/httptrading/api/{instance_id:\w{16,32}}/market/quote', QuoteView),
    ]
    return apis


def run(
        host: str,
        port: int,
        brokers: list[BaseBroker],
        std_apis: Callable[[], list[web.RouteDef]] = None,
        extend_apis: list[web.RouteDef] = None,
        token_header: str = 'HT-TOKEN',
        **kwargs
) -> None:
    """
    @param host: 监听地址
    @param port: 监听端口
    @param brokers: 需要控制的交易通道对象列表
    @param std_apis: 如果需要替换默认提供的接口, 这里提供工厂函数的回调
    @param extend_apis: 如果需要增加自定义接口, 这里传入 RouteDef 列表
    @param token_header: 定制 token 凭据的 header 键名
    @param kwargs: 其他的参数将传给 aiohttp.web.run_app 函数
    """
    app = web.Application(
        middlewares=[
            create_auth_middleware(token_header=token_header),
            exception_middleware,
        ],
    )

    apis = (std_api_factory if std_apis is None else std_apis)()

    if extend_apis:
        apis.extend(extend_apis)

    app.add_routes(apis)

    async def _on_startup(app):
        HttpTradingView.set_brokers(brokers)
        for broker in brokers:
            await broker.start()

    async def _on_shutdown(app):
        for broker in brokers:
            await broker.shutdown()

    app.on_startup.append(_on_startup)
    app.on_shutdown.append(_on_shutdown)
    web.run_app(
        app,
        host=host,
        port=port,
        **kwargs
    )


__all__ = [
    'run',
    'std_api_factory',
    'HttpTradingView',
]