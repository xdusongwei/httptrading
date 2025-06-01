import enum
from datetime import datetime
from dataclasses import dataclass, field


class TradeType(enum.Enum):
    Securities = enum.auto()
    Cryptocurrencies = enum.auto()


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
    根据 ticker 设置交易标的的代码;
    对于支持多个市场的交易通道, 例如证券, 需要额外提供 region 加以区分标的的所属市场.
    """
    trade_type: TradeType
    ticker: str
    region: str

    @property
    def unique_pair(self):
        return self.trade_type, self.ticker, self.region,

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
]
