# httptrading

```shell
pip install httptrading
```

项目的用途
--------

市面上有很多交易通道或者加密货币交易所提供了行情、交易的接口, 
不同的接口提供方提供各自的接入方式, 如果你同时使用多家的接口服务, 
必然需要兼容诸如标的代码格式, 报价单位类型, 下单参数, 判断订单生命周期, 接口限速.

另外的, 混合接入多家 SDK 在一个项目, 会遇到一些兼容性问题: 
包里面的依赖互相版本冲突, 
或者部署的平台、二进制模块发布导致有限 python 的版本支持.

所以, 这个项目打算使用一套统一格式的 http 接口, 把各种接口提供方的调用隐藏, 缺点是每家的功能均会有限制.
支持一些主要的交易动作, 比如查特定持仓, 可用资金, 下限价单以及市价单,
也可以基于基类, 自行编写自定义的其他交易平台接口.

针对兼容性问题, 则可以把不兼容的 SDK 放在不同进程中, 用特定的启动脚本运行, 
如果做得好一点, 再用反向代理软件封装端口的流量.

如何使用
-------

这个项目默认支持如下交易平台:

| 交易通道 | 方式                                               |
|------|--------------------------------------------------|
| 盈透证券 | [ib-insync](https://pypi.org/project/ib-insync/) |
| 富途证券 | [futu-api](https://pypi.org/project/futu-api/)   |
| 长桥证券 | [longport](https://pypi.org/project/longport/)   |
| 老虎证券 | [tigeropen](https://pypi.org/project/tigeropen/) |

本项目支持它们的原理是在函数中而非模块级引用相关 SDK 的包, 
即使得这个项目没有要求必须安装这些依赖.
使用者需要使用到哪个平台, 需要自行在工程项目中安装其 SDK 方能正常工作, 
也就是说使用者根据自身情况自行选择和解决 SDK 兼容性问题.

SDK客户端连通(ping)、交易相关功能:

| 交易通道 | ping | 可用资金 | 查持仓 | 下单 | 撤单 | 查订单 |
|------|------|------|-----|----|----|-----|
| 盈透证券 | ✅    | ✅    | ✅   | ✅  | ✅  | ✅   |
| 富途证券 | ✅    | ✅    | ✅   | ✅  | ✅  | ✅   |
| 长桥证券 | -    | ✅    | ✅   | ✅  | ✅  | ✅   |
| 老虎证券 | -    | ✅    | ✅   | ✅  | ✅  | ✅   |

交易品种范围:

| 交易通道 | 美股正股 | 港股正股 | A股正股 |
|------|------|------|------|
| 盈透证券 | ✅    | 不确定  | ❌    | 
| 富途证券 | ✅    | 不确定  | ❌    | 
| 长桥证券 | ✅    | 不确定  | ❌    | 
| 老虎证券 | ✅    | 不确定  | ❌    | 

市场、报价相关功能:

| 交易通道 | 市场状态 | 报价快照 |
|------|------|------|
| 盈透证券 | ❌    | 不支持  |
| 富途证券 | ✅    | ✅    |
| 长桥证券 | ❌    | ✅    |
| 老虎证券 | ✅    | ✅    |


报价品种范围:

| 交易通道 | 美股正股 | 港股正股 | A股正股 |
|------|------|------|------|
| 盈透证券 | ❌    | ❌    | ❌    | 
| 富途证券 | ✅    | 不确定  | ✅    | 
| 长桥证券 | ✅    | 不确定  | ✅    | 
| 老虎证券 | ✅    | 不确定  | 不支持  | 

使用须知
-------

```
项目以及使用到的框架以及实际的部署方式可能存在弱点和缺陷, 因此项目仅供学习参考，不为任何盈亏负有责任。

各个交易平台提供的接口服务, 并不能保证提供的数据准确、交易可靠以及有效, 参与投资请意识到技术上的风险隐患.
```

实例和验证
--------

在配置交易通道前, 需要自行产生16到32字符的唯一实例id, 一个实例id对应一家交易通道配置.
实例id会包含在请求的 path 中, 防止接口地址被嗅探.

另外使用实例id是为了区分比如你有多家相同交易通道的账户, 这样如果用交易通道做类别就没办法多账户控制了.

除了实例id, 交易通道需要配置一组16到64字符token, 在请求中携带 `HT-TOKEN` header 来确保可以正常操作此实例.

例如下面是一个启动脚本:

```python
from httptrading import run, Futu


# 交易通道的参数
args = {
    'host': '127.0.0.1',
    'port': 8888,
    'trade_env': 'SIMULATE',
}
futu = Futu(
    broker_args=args,
    instance_id='WyLqtMhDvAnBb6a3',
    tokens=['Vt5UCW2sLBvgPXjR', ],
)
run(
    host='0.0.0.0',
    port=8080,
    brokers=[futu, ],
)

```

这样携带某项 token 访问 http://127.0.0.1:8080/httptrading/api/WyLqtMhDvAnBb6a3/market/state 可以得到响应.


接口说明
-------

正常返回的接口会包含如下的字段:

```json lines
{
	"type": "apiResponse", // 节的类型
	"instanceId": "ggUqPZbSKuQ7Ewsk", // 实例id
	"broker": "futu", // 通道的类型
	"brokerDisplay": "富途证券", // 通道的展示名称
	"time": "2025-05-28T05:20:07.062298+00:00", // 服务器时间
	"ex": null // 引发异常的文字描述
}
```

### ping

GET /httptrading/api/{instanceId}/ping/state

用于检测和修复 SDK 客户端的连接,
某些需要维持客户端连接的交易通道需要具体实现这个接口.

对于用户, 最好在操作交易通道前测试这个接口, 避免下单时出错引起更复杂的人工检查核对.

```json lines
{
	"type": "apiResponse",
	"instanceId": "ggUqPZbSKuQ7Ewsk",
	"broker": "futu",
	"brokerDisplay": "富途证券",
	"time": "2025-05-28T05:20:07.062298+00:00",
	"ex": null,
	"pong": true
}
```

如果 pong 是 false, 表明客户端测试是失败的.


### 报价快照

GET /httptrading/api/{instanceId}/market/quote

需要在 query 提供 Contract 参数:

| 参数        | 说明      | 举例                 |
|-----------|---------|--------------------|
| tradeType | 说明标的的品种 | Securities: 证券     |
| ticker    | 代码      | QQQ, 00700, 000001 |
| region    | 以国家区分代码 | US, HK, CN         |

举例 ?tradeType=Securities&region=CN&ticker=000001 参数的结果:
```json lines
{
	"type": "apiResponse",
	"instanceId": "ggUqPZbSKuQ7Ewsk",
	"broker": "futu",
	"brokerDisplay": "富途证券",
	"time": "2025-05-28T05:25:19.542635+00:00",
	"ex": null,
	"quote": {
		"type": "quote",
		"contract": {
			"type": "contract",
			"tradeType": "Securities",
			"region": "CN",
			"ticker": "000001"
		},
		"currency": "CNY",  // 币种
		"isTradable": true,  // 此时是否可交易, 比如受到停牌熔断影响
		"latest": 11.53, // 最新价
		"preClose": 11.49, // 昨日收盘价 
		"highPrice": 11.55, // 日最高价
		"lowPrice": 11.44, // 日最低价
		"openPrice": 11.5, // 开盘价
		"timestamp": 1748409918000 // 快照行情的时间
	}
}
```


### 市场状态

GET /httptrading/api/{instanceId}/market/state

```json lines
{
    "type": "apiResponse",
    "instanceId": "ggUqPZbSKuQ7Ewsk",
    "broker": "futu",
    "brokerDisplay": "富途证券",
    "time": "2025-05-28T05:33:42.543109+00:00",
    "ex": null,
    "marketStatus": {
        "type": "marketStatusMap",
        "securities": { // 证券类市场状态, 以 region 为键的结构
            "US": {
                "type": "marketStatus",
                "region": "US",
                "originStatus": "AFTER_HOURS_END", // 交易通道原始市场状态
                "unifiedStatus": "CLOSED" // 统一映射的定义
            },
            "CN": {
                "type": "marketStatus",
                "region": "CN",
                "originStatus": "AFTERNOON",
                "unifiedStatus": "RTH"
            },
            "HK": {
                "type": "marketStatus",
                "region": "HK",
                "originStatus": "AFTERNOON",
                "unifiedStatus": "RTH"
            }
        }
    }
}
```

统一映射的枚举:

| 枚举          | 说明            |
|-------------|---------------|
| UNKNOWN     | 开盘前, 不能映射到的状态 |
| OVERNIGHT   | 夜盘            |
| PRE_HOURS   | 盘前            |
| RTH         | 正常交易时段        |
| REST        | 休市            |
| AFTER_HOURS | 盘后            |
| CLOSED      | 收盘            |


### 可用资金

GET /httptrading/api/{instanceId}/cash/state

```json lines
{
	"type": "apiResponse",
	"instanceId": "ggUqPZbSKuQ7Ewsk",
	"broker": "futu",
	"brokerDisplay": "富途证券",
	"time": "2025-05-28T05:39:10.743937+00:00",
	"ex": null,
	"cash": {
		"type": "cash",
		"currency": "USD",
		"amount": 30877.499
	}
}
```


### 持仓列表

GET /httptrading/api/{instanceId}/position/state

```json lines
{
	"type": "apiResponse",
	"instanceId": "ggUqPZbSKuQ7Ewsk",
	"broker": "futu",
	"brokerDisplay": "富途证券",
	"time": "2025-05-28T05:40:58.470655+00:00",
	"ex": null,
	"positions": [
		{
			"type": "position",
			"broker": "futu",
			"brokerDisplay": "富途证券",
			"contract": {
				"type": "contract",
				"tradeType": "Securities",
				"region": "US",
				"ticker": "QQQ"
			},
			"unit": "Share",
			"currency": "USD",
			"qty": 2400
		}
	]
}
```


### 下单

POST /httptrading/api/{instanceId}/order/place

需要提交 json 格式的 Body, 其中的参数如下:

| 参数          | 说明       | 举例                                               |
|-------------|----------|--------------------------------------------------|
| tradeType   | 说明标的的品种  | Securities: 证券                                   |
| ticker      | 代码       | QQQ, 00700, 000001                               |
| region      | 以国家区分代码  | US, HK, CN                                       |
| price       | 限价       | 市价单不填此项                                          |
| qty         | 订单数量, 整数 |                                                  |
| orderType   | 订单类型     | Limit: 限价单<br>Market: 市价单                        |
| timeInForce | 订单的有效期   | DAY: 日内<br>GTC: 撤销前有效                            |
| lifecycle   | 订单交易时段   | RTH: 正常交易时段<br>ETH: 正常交易时段+盘前盘后<br>OVERNIGHT: 夜盘 |
| direction   | 买卖方向     | BUY, SELL                                        |

例如 Body 可以是这样:
```json lines
{
	"tradeType": "Securities",
	"ticker": "AAPL",
	"region": "US",
	"price": 200.00,
	"qty": 12,
	"orderType": "Limit",
	"timeInForce": "DAY",
	"lifecycle": "ETH",
	"direction": "BUY"
}
```

产生的响应:
```json lines
{
	"type": "apiResponse",
	"instanceId": "ggUqPZbSKuQ7Ewsk",
	"broker": "futu",
	"brokerDisplay": "富途证券",
	"time": "2025-05-28T06:01:23.575968+00:00",
	"ex": null,
	"orderId": "69788888", // 订单号
	"args": { // 传递的参数
		"tradeType": "Securities",
		"ticker": "AAPL",
		"region": "US",
		"price": 200,
		"qty": 12,
		"orderType": "Limit",
		"timeInForce": "DAY",
		"lifecycle": "ETH",
		"direction": "BUY"
	}
}
```

### 撤单

POST /httptrading/api/{instanceId}/order/cancel

需要提交 json 格式的 Body, 其中包含 orderId 字段来传递需要撤单的订单号码.

```json lines
{
	"type": "apiResponse",
	"instanceId": "ggUqPZbSKuQ7Ewsk",
	"broker": "futu",
	"brokerDisplay": "富途证券",
	"time": "2025-05-28T06:45:41.645855+00:00",
	"ex": null // ex 字段将记录异常原因
}
```


### 查询单个订单

GET /httptrading/api/{instanceId}/order/state?orderId={订单号}

接口会返回成交和订单状态相关的信息, 订单的更多信息, 需要在下单时自行保存.


```json lines
{
	"type": "apiResponse",
	"instanceId": "ggUqPZbSKuQ7Ewsk",
	"broker": "futu",
	"brokerDisplay": "富途证券",
	"time": "2025-05-28T05:59:29.984021+00:00",
	"ex": null,
	"order": {
		"type": "order",
		"orderId": "6278888",
		"currency": "USD",
		"qty": 12, // 订单数量
		"filledQty": 0, // 已成交数量
		"avgPrice": 0, // 成交价
		"errorReason": "", // 如果订单异常, 这里记录错误信息
		"isCanceled": false, // 是否已撤销
		"isFilled": false, // 是否全部成交
		"isCompleted": false // 全部成交 或者 有异常 或者 已撤销, 亦等价于不可撤的标志
	}
}
```

```
富途证券不支持查询单个订单. 意味着订单结束周期的交易日之后, 将查不到订单.
```

交易通道的参数
------------

### 富途证券

```python
import httptrading
args = {
    'host': '127.0.0.1', # OpenD 地址
    'port': 12345, # OpenD 地址
    'trade_env': 'REAL', # 实盘填REAL, 模拟盘填SIMULATE
    'pk_path': '', # OpenD非本机部署需要填证书路径
    # 实盘操作订单需要解锁密码
    # https://openapi.futunn.com/futu-api-doc/trade/unlock.html
    # 这里储存密码的md5值, 使用命令 
    # macos: md5 -s "123456" 
    # linux: echo -n "123456" | md5sum
    'unlock_pin': '',
}
broker = httptrading.Futu(args, ...)
```

### 长桥证券

```python
import httptrading
args = {
    'app_key': '...',
    'app_secret': '...',
    # 需要制作一个 token 文件供服务读写
    # 具体情况见 httptrading/broker/longbridge.py
    'token_file': '...',
    # 是否尝试自动刷新即将过期的 token
    'auto_refresh_token': True,
}
broker = httptrading.LongBridge(args, ...)
```

### 老虎证券

```python
import httptrading
args = {
    'tiger_id': '...',
    'account': '...',
    'pk_path': '...',
}
broker = httptrading.Tiger(args, ...)
```
### 盈透证券(TWS方式)

```python
import httptrading
args = {
    'host': '127.0.0.1',
    'port': 1234,
    'timeout': 8,
    'client_id': 123456,  # TWS中设置的号码
    'account_id': 'U...', # U开头的账户号
}
broker = httptrading.InteractiveBrokers(args, ...)
```

开发自定义交易通道
---------------

```python
from httptrading import *
from httptrading.model import *


@broker_register('myApi', 'XX证券')
class MyTradingApi(BaseBroker):
    # 根据需要的功能实现接口
    # 如果 sdk 提供的方式会阻塞 eventloop, 需要使用 self.call_sync 方法传入阻塞方法
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

    async def market_status(self) -> dict[str, dict[str, MarketStatus]]:
        raise NotImplementedError
```