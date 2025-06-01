import json
from datetime import datetime, UTC
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
        ticker = params.get('ticker', '--')
        contract = Contract(
            trade_type=trade_type,
            region=region,
            ticker=ticker,
        )
        return contract

    @classmethod
    def json_default(cls, obj):
        if isinstance(obj, Position):
            return {
                'type': 'position',
                'broker': obj.broker,
                'brokerDisplay': obj.broker_display,
                'contract': cls.json_default(obj.contract),
                'unit': obj.unit.name,
                'currency': obj.currency,
                'qty': obj.qty,
            }
        if isinstance(obj, Contract):
            return {
                'type': 'contract',
                'tradeType': obj.trade_type.name,
                'region': obj.region,
                'ticker': obj.ticker,
            }
        if isinstance(obj, Cash):
            return {
                'type': 'cash',
                'currency': obj.currency,
                'amount': obj.amount,
            }
        if isinstance(obj, MarketStatus):
            return {
                'type': 'marketStatus',
                'region': obj.region,
                'originStatus': obj.origin_status,
                'unifiedStatus': obj.unified_status.name,
            }
        if isinstance(obj, Quote):
            return {
                'type': 'quote',
                'contract': cls.json_default(obj.contract),
                'currency': obj.currency,
                'isTradable': obj.is_tradable,
                'latest': obj.latest,
                'preClose': obj.pre_close,
                'highPrice': obj.high_price,
                'lowPrice': obj.low_price,
                'openPrice': obj.open_price,
                'timestamp': int(obj.time.timestamp() * 1000),
            }
        if isinstance(obj, Order):
            return {
                'type': 'order',
                'orderId': obj.order_id,
                'currency': obj.currency,
                'qty': obj.qty,
                'filledQty': obj.filled_qty,
                'avgPrice': obj.avg_price,
                'errorReason': obj.error_reason,
                'isCanceled': obj.is_canceled,
                'isFilled': obj.is_filled,
                'isCompleted': obj.is_completed,
            }
        raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")

    @classmethod
    def dumps(cls, obj):
        return json.dumps(obj, default=cls.json_default)

    @classmethod
    def response_obj(cls, obj):
        return web.Response(text=cls.dumps(obj), content_type='application/json')

    @classmethod
    def response_api(cls, broker: BaseBroker, args: dict = None, ex: Exception = None):
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


@web.middleware
async def auth_middleware(request: web.Request, handler):
    instance_id = request.match_info.get('instance_id', '')
    token = request.headers.get('HT-TOKEN', '')
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


def run(
        host: str,
        port: int,
        brokers: list[BaseBroker],
) -> None:
    app = web.Application(
        middlewares=[
            auth_middleware,
            exception_middleware,
        ],
    )
    app.add_routes(
        [
            web.view(r'/httptrading/api/{instance_id:\w{16,32}}/order/place', PlaceOrderView),
            web.view(r'/httptrading/api/{instance_id:\w{16,32}}/order/state', OrderStateView),
            web.view(r'/httptrading/api/{instance_id:\w{16,32}}/order/cancel', CancelOrderView),
            web.view(r'/httptrading/api/{instance_id:\w{16,32}}/cash/state', CashView),
            web.view(r'/httptrading/api/{instance_id:\w{16,32}}/position/state', PositionView),
            web.view(r'/httptrading/api/{instance_id:\w{16,32}}/ping/state', PlugInView),
            web.view(r'/httptrading/api/{instance_id:\w{16,32}}/market/state', MarketStatusView),
            web.view(r'/httptrading/api/{instance_id:\w{16,32}}/market/quote', QuoteView),
        ]
    )

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
    )
