import asyncio
import importlib
from abc import ABC
from typing import Type, Callable, Any
from httptrading.model import *


class BaseBroker(ABC):
    def __init__(self, broker_args: dict = None, instance_id: str = None, tokens: list[str] = None):
        self.detect_package()
        self.broker_args = broker_args or dict()
        self.instance_id = instance_id or ''
        self.tokens: set[str] = set(tokens or list())
        assert '' not in self.tokens

    @property
    def broker_name(self):
        return BrokerRegister.get_meta(type(self)).name

    @property
    def broker_display(self):
        return BrokerRegister.get_meta(type(self)).display

    def detect_package(self):
        pkg_info = BrokerRegister.get_meta(type(self)).detect_package
        if pkg_info is None:
            return
        try:
            importlib.import_module(pkg_info.import_name)
        except ImportError:
            raise ImportError(f'需要安装依赖包: {pkg_info.pkg_name}')

    async def start(self):
        pass

    async def shutdown(self):
        pass

    async def call_sync(self, f: Callable[[], Any]):
        try:
            r = await asyncio.get_running_loop().run_in_executor(None, f)
            return r
        except Exception as ex:
            raise BrokerError(self, ex)

    async def call_async(self, coro):
        try:
            r = await coro
            return r
        except Exception as ex:
            raise BrokerError(self, ex)

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
        raise NotImplementedError

    async def order(self, order_id: str) -> Order:
        raise NotImplementedError

    async def cancel_order(self, order_id: str):
        raise NotImplementedError

    async def positions(self) -> list[Position]:
        raise NotImplementedError

    async def cash(self) -> Cash:
        raise NotImplementedError

    async def ping(self) -> bool:
        return True

    async def quote(self, contract: Contract) -> Quote:
        raise NotImplementedError

    async def market_status(self) -> dict[TradeType, dict[str, MarketStatus] | str]:
        """
        报告交易通道提供的市场状态,
        返回一个双层字典,
        外层字典是以交易品种分类的结构, 比如 TradeType.Securities,
        内层的字典是按国家代码区分的各个市场状态的结构, 比如 "US".
        """
        raise NotImplementedError


class SecuritiesBroker(BaseBroker):
    @classmethod
    def contract_to_tz(cls, contract: Contract) -> str:
        region = contract.region
        match region:
            case 'CN':
                return 'Asia/Shanghai'
            case 'HK':
                return 'Asia/Hong_Kong'
            case 'US':
                return 'US/Eastern'
            case _:
                raise Exception(f'不能映射{contract}为已知时区')

    @classmethod
    def contract_to_currency(cls, contract: Contract) -> str | None:
        region = contract.region
        match region:
            case 'CN':
                return 'CNY'
            case 'HK':
                return 'HKD'
            case 'US':
                return 'USD'
            case _:
                raise Exception(f'不能映射{contract}为已知币种')


class BrokerRegister:
    _D: dict[str, Type[BaseBroker]] = dict()
    _META: dict[str, BrokerMeta] = dict()

    @classmethod
    def register(cls, broker_class: Type[BaseBroker], name: str, display: str, detect_pkg: DetectPkg = None):
        type_name = broker_class.__name__
        if type_name in cls._D:
            raise ValueError(f"Duplicate broker class name '{type_name}'")
        cls._D[type_name] = broker_class
        cls._META[type_name] = BrokerMeta(name=name, display=display, detect_package=detect_pkg)

    @classmethod
    def get_meta(cls, broker_class: Type[BaseBroker]) -> BrokerMeta:
        return BrokerRegister._META.get(broker_class.__name__)


def broker_register(name: str, display: str, detect_pkg: DetectPkg = None):
    def decorator(cls: Type[BaseBroker]):
        BrokerRegister.register(cls, name, display, detect_pkg)
        return cls
    return decorator


class BrokerError(ValueError):
    def __init__(self, broker: BaseBroker, ex, *args, **kwargs):
        self.broker = broker
        super().__init__(f'[{broker.instance_id}]<{broker.broker_name} {broker.broker_display}>异常: {ex}')


__all__ = [
    'BaseBroker',
    'SecuritiesBroker',
    'broker_register',
    'BrokerError',
]
