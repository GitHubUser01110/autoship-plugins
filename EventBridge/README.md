# 事件桥接器插件 (WebSocket版本)

## 概述
事件桥接器插件是智能无人船系统的核心基础设施组件，基于WebSocket协议实现event_bus事件系统与物理设备之间的双向消息转换和路由。作为系统级插件，它为所有设备控制插件提供底层长连接通信能力。

## 核心功能

### 1. 下行通信（Event → WebSocket）
- 订阅 `device.command.**` 事件
- 将事件数据转换为WebSocket消息格式
- 根据target_device路由到对应设备连接
- 自动生成消息序列号

### 2. 上行通信（WebSocket → Event）
- 监听WebSocket端口接收设备连接
- 解析WebSocket消息并转换为事件
- 发布设备响应、状态、传感器数据、报警等事件
- 自动识别和管理设备连接

### 3. 连接管理
- 支持多设备长连接
- 动态设备注册（通过source字段识别）
- 连接状态监控
- 自动处理断线

### 4. 心跳机制
- 接收设备心跳上报
- 自动发送心跳应答
- 发布心跳事件
- 连接保活

## 架构位置

```
应用层插件（导航、控制等）
    ↓ 发布 device.command.* 事件
Event Bus（事件总线）
    ↓ 订阅/发布
事件桥接器插件（本插件）
    ↓ WebSocket长连接
物理设备（Jetson等）
```

## WebSocket协议特性

### 与UDP版本的区别

| 特性 | UDP版本 | WebSocket版本 |
|------|---------|---------------|
| 连接类型 | 无连接 | 长连接 |
| 设备配置 | 需要host:port | 只需设备名 |
| 设备识别 | 地址学习 | source字段注册 |
| 消息可靠性 | 不保证 | TCP保证 |
| 双向通信 | 需轮询 | 全双工 |
| 连接状态 | 无法检测 | 可检测上下线 |

### 消息流程

#### 设备连接流程
```
1. 设备作为WebSocket客户端连接服务器
2. 设备发送带source字段的消息
3. 服务器注册设备连接映射
4. 连接建立完成
```

#### 命令下发流程
```
1. 应用层发布 device.command.* 事件
2. 桥接器查找设备连接
3. 通过WebSocket发送命令
4. 设备接收并执行
5. 设备返回响应
6. 桥接器发布结果事件
```

## 订阅事件

### device.command.** (通配符)
接收所有设备命令事件并转发到WebSocket

**事件数据格式**:
```json
{
    "target_device": "usb2xxx_1",
    "device_id": "pump_0",
    "action": "on",
    "value": null
}
```

**转换为WebSocket消息**:
```json
{
    "type": "device_command",
    "seq": 123,
    "device_id": "pump_0",
    "command": {"state": true},
    "timestamp": 1696500000.0
}
```

## 发布事件

### device.command.result
设备命令执行结果
```json
{
    "device_id": "pump_0",
    "result": {
        "status": "success",
        "message": "命令执行成功"
    },
    "source": "usb2xxx_1"
}
```

### device.status.{source}
设备状态更新（source为设备名称）
```json
{
    "device_id": "propeller_0",
    "status": {
        "speed": 50,
        "power": 80
    },
    "source": "usb2xxx_1"
}
```

### sensor.{sensor_type}
传感器数据
```json
{
    "sensor_type": "gps",
    "latitude": 31.23,
    "longitude": 121.47
}
```

### alert.{level}
设备报警（level: info/warning/critical）
```json
{
    "level": "critical",
    "message": "设备故障",
    "device_id": "pump_0"
}
```

### system.heartbeat
设备心跳
```json
{
    "device": "usb2xxx_1",
    "device_count": 5,
    "timestamp": 1696500000.0
}
```

### bridge.started
桥接器启动通知
```json
{
    "plugin_id": "event_bridge",
    "listen_port": 13000,
    "devices": ["usb2xxx_1"]
}
```

### bridge.stopped
桥接器停止通知
```json
{
    "plugin_id": "event_bridge"
}
```

## 接口方法

### get_bridge_status()
获取桥接器运行状态

**输入**: 无

**输出**:
```json
{
    "running": true,
    "listen_port": 13000,
    "configured_devices": ["usb2xxx_1"],
    "connected_devices": ["usb2xxx_1"],
    "client_count": 1,
    "statistics": {
        "uptime": 3600.5,
        "messages_sent": 1234,
        "messages_received": 5678,
        "commands_forwarded": 456,
        "errors": 2
    }
}
```

### get_device_connection(device_name)
获取设备连接状态

**输入**:
```json
{
    "device_name": "usb2xxx_1"
}
```

**输出**:
```json
{
    "success": true,
    "connected": true,
    "remote_address": "('127.0.0.1', 54321)"
}
```

### broadcast_message(message)
广播消息到所有客户端

**输入**:
```json
{
    "message": {
        "type": "notification",
        "data": "系统消息"
    }
}
```

**输出**:
```json
{
    "success": true,
    "message": "消息已广播到 2 个客户端"
}
```

## 配置参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| listen_port | number | 13000 | WebSocket监听端口 |
| devices | object | {...} | 设备配置表 |

### devices 配置格式

```json
{
    "usb2xxx_1": {
        "description": "主控设备"
    },
    "usb2xxx_2": {
        "description": "备用设备"
    }
}
```

**注意**: devices配置只需要设备名称和描述，不需要配置IP地址和端口（设备主动连接）

## 消息转换映射

### 动作到命令映射

| Action | WebSocket Command |
|--------|-------------------|
| on | `{"state": true}` |
| off | `{"state": false}` |
| set_speed | `{"speed": value}` |
| on_fwd | `{"state": true, "direction": true}` |
| on_rev | `{"state": true, "direction": false}` |
| start_forward | `{"action": "start_forward", "speed": value}` |
| start_backward | `{"action": "start_backward", "speed": value}` |
| stop | `{"action": "stop"}` |

## 设备端实现要求

### 1. WebSocket客户端连接
```python
import asyncio
import websockets
import json

async def connect_to_bridge():
    uri = "ws://server_ip:13000"
    async with websockets.connect(uri) as websocket:
        # 连接成功后发送消息
        await send_heartbeat(websocket)
```

### 2. 消息必须包含source字段
```python
# 正确的消息格式
message = {
    "type": "heartbeat_report",
    "source": "usb2xxx_1",  # 重要：用于设备注册
    "data": {
        "device_count": 5,
        "timestamp": time.time()
    }
}
await websocket.send(json.dumps(message))
```

### 3. 实现心跳机制
```python
async def heartbeat_loop(websocket):
    while True:
        await websocket.send(json.dumps({
            "type": "heartbeat_report",
            "source": "usb2xxx_1",
            "data": {
                "device_count": 5,
                "timestamp": time.time()
            }
        }))
        await asyncio.sleep(5)
```

### 4. 处理服务器命令
```python
async def message_handler(websocket):
    async for message in websocket:
        data = json.loads(message)
        
        if data.get('type') == 'device_command':
            # 执行命令
            result = execute_command(data['device_id'], data['command'])
            
            # 发送响应
            await websocket.send(json.dumps({
                "type": "command_response",
                "source": "usb2xxx_1",
                "device_id": data['device_id'],
                "data": result
            }))
```

## 使用示例

### 1. 安装依赖
```bash
pip install websockets
```

### 2. 安装和启动插件
```python
# 安装插件
plugin_manager.install_plugin("event_bridge")

# 启用插件（会自动启动WebSocket服务器）
plugin_manager.enable_plugin("event_bridge")
```

### 3. 获取状态
```python
status = plugin_manager.call_plugin_interface(
    "event_bridge",
    "get_bridge_status"
)
print(f"桥接器运行状态: {status}")
print(f"已连接设备: {status['connected_devices']}")
```

### 4. 查询设备连接
```python
conn = plugin_manager.call_plugin_interface(
    "event_bridge",
    "get_device_connection",
    {"device_name": "usb2xxx_1"}
)
if conn['connected']:
    print(f"设备已连接: {conn['remote_address']}")
else:
    print("设备未连接")
```

### 5. 广播消息
```python
plugin_manager.call_plugin_interface(
    "event_bridge",
    "broadcast_message",
    {
        "message": {
            "type": "system_notification",
            "data": "系统维护通知"
        }
    }
)
```

### 6. 订阅设备反馈
```python
from event_bus import event_bus

def on_command_result(event):
    data = event.data
    print(f"设备 {data['device_id']} 命令结果: {data['result']}")

event_bus.subscribe("device.command.result", on_command_result)
```

## 统计信息

桥接器实时统计以下信息：
- **uptime**: 运行时长（秒）
- **messages_sent**: 已发送WebSocket消息数
- **messages_received**: 已接收WebSocket消息数
- **commands_forwarded**: 已转发命令数
- **errors**: 错误次数
- **client_count**: 当前连接客户端数
- **connected_devices**: 已注册设备列表

## 错误处理

### 常见错误

1. **端口被占用**
   - 错误: `[Errno 48] Address already in use`
   - 解决: 检查端口是否被其他程序占用，或修改listen_port配置

2. **设备未配置**
   - 错误: `未知设备: xxx`
   - 解决: 在devices配置中添加设备名称

3. **设备未连接**
   - 错误: `设备未连接: xxx`
   - 解决: 确保设备端WebSocket客户端已启动并成功连接

4. **JSON解析失败**
   - 错误: `JSON解析失败`
   - 解决: 检查WebSocket消息格式是否正确

5. **事件循环未初始化**
   - 错误: `事件循环未初始化`
   - 解决: 确保插件已正确启用

### 错误统计

所有错误都会被记录到统计信息中，可以通过 `get_bridge_status()` 查询。

## 依赖关系

### Python依赖
```
websockets>=12.0
```

### 本插件被依赖于
- 设备统一控制插件 (DeviceControl)
- 其他需要与物理设备通信的插件

### 本插件依赖于
- event_bus (事件总线系统)
- Python标准库: asyncio, json, threading, time

## 性能特性

1. **异步处理**: 基于asyncio的高性能异步架构
2. **连接复用**: WebSocket长连接减少握手开销
3. **全双工通信**: 支持服务器主动推送
4. **线程安全**: 使用锁保护共享数据
5. **资源清理**: 插件禁用时自动清理所有连接

## 调试技巧

### 1. 查看连接状态
```python
status = plugin_manager.call_plugin_interface("event_bridge", "get_bridge_status")
print(f"配置设备: {status['configured_devices']}")
print(f"已连接设备: {status['connected_devices']}")
print(f"客户端数: {status['client_count']}")
```

### 2. 测试设备连接
```python
# 使用wscat测试连接
# npm install -g wscat
# wscat -c ws://localhost:13000

# 发送测试消息
{
    "type": "heartbeat_report",
    "source": "test_device",
    "data": {}
}
```

### 3. 广播测试消息
```python
plugin_manager.call_plugin_interface(
    "event_bridge",
    "broadcast_message",
    {
        "message": {
            "type": "test",
            "data": "ping"
        }
    }
)
```

### 4. 监控日志
插件会输出详细日志，包括：
- 客户端连接/断开
- 设备注册
- 收到的命令事件
- 发送的WebSocket消息
- 接收的WebSocket消息
- 转发的事件
- 错误信息

## 注意事项

1. **设备端实现**: 设备必须作为WebSocket客户端主动连接服务器
2. **source字段必需**: 所有设备消息必须包含source字段用于设备识别和注册
3. **心跳保活**: 设备需要定期发送心跳保持连接活跃
4. **重连机制**: 设备端应实现断线自动重连
5. **消息格式**: 严格遵循JSON格式，包含type和source字段
6. **系统级插件**: 建议在所有设备控制插件之前启动
7. **单例运行**: 同一listen_port只能有一个桥接器实例

## 迁移指南（从UDP版本）

### 配置文件变更
```json
// UDP版本
{
    "devices": {
        "usb2xxx_1": {
            "host": "192.168.69.67",
            "port": 12345
        }
    }
}

// WebSocket版本
{
    "devices": {
        "usb2xxx_1": {
            "description": "主控设备"
        }
    }
}
```

### 设备端代码变更
```python
# UDP版本 - 设备作为服务器
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(('0.0.0.0', 12345))

# WebSocket版本 - 设备作为客户端
async def main():
    async with websockets.connect('ws://server:13000') as ws:
        # 发送消息
        await ws.send(json.dumps(message))
```

## 版本历史

- v1.0.0 (2025-10-06)
  - WebSocket版本发布
  - 从UDP改为WebSocket长连接
  - 简化设备配置（无需host:port）
  - 新增连接状态管理
  - 新增广播功能
  - 改进错误处理和日志输出