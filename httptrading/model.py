import enum
from typing import Type
from datetime import datetime
from dataclasses import dataclass, field


class TradeType(enum.Enum):
    Securities = enum.auto()
    Cryptocurrencies = enum.auto()
    Indexes = enum.auto()   # 指数
    Currencies = enum.auto()  # 货币对
    Yields = enum.auto()  # 利率


class Unit(enum.Enum):
    Share = enum.auto()
    RoundLot = enum.auto()
    Satoshi = enum.auto()


class OrderType(enum.Enum):
    """
    订单的类型
    """
    Limit = enum.auto() # 限价单
    Market = enum.auto() # 市价单


class TimeInForce(enum.Enum):
    """
    订单的有效期, 一般的交易通道均支持当日有效和取消前有效
    """
    DAY = enum.auto()
    GTC = enum.auto()


class Lifecycle(enum.Enum):
    """
    订单交易时段
    """
    RTH = enum.auto() # 盘中
    ETH = enum.auto() # 盘中 + 盘前盘后
    OVERNIGHT = enum.auto() # 仅夜盘


class UnifiedStatus(enum.Enum):
    UNKNOWN = enum.auto() # 已知信息不能映射到的状态
    OVERNIGHT = enum.auto() # 夜盘
    PRE_HOURS = enum.auto() # 盘前
    RTH = enum.auto() # 正常交易时段
    REST = enum.auto()  # 休市
    AFTER_HOURS = enum.auto() # 盘后
    CLOSED = enum.auto() # 收盘


@dataclass(frozen=True)
class Contract:
    """
    Contract 定义了交易品种的精确描述.
    根据交易种类, 区分为证券和加密货币;
    根据 symbol 设置交易标的的代码;
    对于支持多个市场的交易通道, 例如证券, 需要额外提供 region 加以区分标的的所属市场.
    """
    trade_type: TradeType
    symbol: str
    region: str

    @property
    def unique_pair(self):
        return self.trade_type, self.symbol, self.region,

    def __hash__(self):
        return self.unique_pair.__hash__()

    def __eq__(self, other):
        if isinstance(other, Contract):
            return self.unique_pair == other.unique_pair
        return False


@dataclass(frozen=True)
class Position:
    broker: str
    broker_display: str
    contract: Contract
    unit: Unit
    currency: str
    qty: int


@dataclass(frozen=True)
class Cash:
    currency: str
    amount: float


@dataclass(frozen=True)
class MarketStatus:
    region: str
    origin_status: str
    unified_status: UnifiedStatus


@dataclass(frozen=True)
class Quote:
    contract: Contract
    currency: str
    is_tradable: bool
    latest: float
    pre_close: float
    open_price: float
    high_price: float
    low_price: float
    time: datetime


@dataclass(frozen=True)
class Order:
    order_id: str
    currency: str
    qty: int
    filled_qty: int = field(default=0)
    avg_price: float = field(default=0.0)
    error_reason: str = field(default='')
    is_canceled: bool = field(default=False)
    # 如果交易通道存在"待取消""已提交取消"的订单状态,
    # 这里需要改变默认值为 True
    is_pending_cancel: bool = field(default=False)

    @property
    def is_filled(self) -> bool:
        is_filled = False
        if self.filled_qty >= self.qty:
            is_filled = True
        return is_filled

    @property
    def is_completed(self) -> bool:
        is_completed = False
        if self.filled_qty >= self.qty:
            is_completed = True
        elif self.is_canceled:
            is_completed = True
        elif self.error_reason:
            is_completed = True
        return is_completed

    @property
    def is_cancelable(self) -> bool:
        is_completed = self.is_completed
        return not is_completed and not self.is_pending_cancel


@dataclass(frozen=True)
class DetectPkg:
    """
    如果需要在 BaseBroker 对象创建时检测相关的 sdk 包是否可以导入,
    这个结构用于在 @broker_register 装饰器的参数中说明需要导入的模块名以及对应包的安装名.
    """
    pkg_name: str
    import_name: str


@dataclass(frozen=True)
class BrokerMeta:
    name: str
    display: str
    detect_package: DetectPkg = None


class JsonDefault:
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
                'symbol': obj.symbol,
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
                'isCancelable': obj.is_cancelable,
            }
        raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


class HtGlobalConfig:
    # 提供一个静态文件目录, 使得当收到推送的订单时将订单落盘为 json 文件.
    # 对于一些没有提供永久查询单个订单接口的交易通道, 访问静态文件可以是查询历史订单的击穿方法.
    # 注意, 落盘的文件名是"{实例ID}-{订单号}.json"格式, 不确定同样一家交易通道, 不同的交易品种之间是否会撞号.
    # 例如, 假如平台先做的证券交易, 后面组了一个期货团队, 然后开放接口团队接入的是两套没有交集的订单系统, 没做订单号码二次映射.
    STREAM_DUMP_FOLDER: str = None
    # 对于一些没有提供永久查询单个订单接口的交易通道, 可以在启动服务时把当时的活动订单更新一遍.
    # 比如重启服务后担心漏掉订单的推送.
    DUMP_ACTIVE_ORDERS: bool = False
    # 如果需要定制接口返回对象的行为, 这里替换为自定义的类
    JSON_DEFAULT: Type[JsonDefault] = JsonDefault


__all__ = [
    'TradeType',
    'Unit',
    'OrderType',
    'TimeInForce',
    'Lifecycle',
    'UnifiedStatus',
    'Contract',
    'Position',
    'Cash',
    'MarketStatus',
    'Quote',
    'Order',
    'DetectPkg',
    'BrokerMeta',
    'JsonDefault',
    'HtGlobalConfig',
]
