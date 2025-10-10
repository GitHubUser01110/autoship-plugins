# 事件桥接器插件 (WebSocket指令流专用)

## 概述
事件桥接器插件是智能无人船系统的核心基础设施组件，基于WebSocket协议实现event_bus事件系统与物理设备之间的**双向指令通信**。

**重要变更**：v2.0.0版本专注于指令流通信，视频流功能已分离到独立的VideoStream插件。

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
事件桥接器插件（本插件 - WebSocket指令流）
    ↓ WebSocket长连接
物理设备（Jetson等）
```

## WebSocket协议特性

### 为什么指令流使用WebSocket？

| 特性 | WebSocket | UDP |
|------|-----------|-----|
| 连接类型 | 长连接 | 无连接 |
| 可靠性 | TCP保证 | 不保证 |
| 双向通信 | 全双工 | 需轮询 |
| 命令响应 | 实时 | 可能丢失 |
| 连接状态 | 可检测 | 无法检测 |
| 适用场景 | **指令控制** | 视频流 |

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
设备状态更新
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

## 接口方法

### get_bridge_status()
获取桥接器运行状态

**输出**:
```json
{
    "running": true,
    "listen_port": 13000,
    "protocol": "websocket",
    "purpose": "command_stream",
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
| listen_port | number | 13000 | WebSocket监听端口（指令流） |
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

# 启用插件
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

## 与VideoStream插件的关系

**EventBridge插件** (本插件)：
- **协议**: WebSocket
- **端口**: 13000
- **功能**: 双向指令通信
- **数据**: 控制命令、状态上报、传感器数据

**VideoStream插件**：
- **协议**: UDP接收 + WebSocket分发
- **端口**: 14000 (UDP视频)、14001 (UDP检测框)、14002 (WebSocket订阅)
- **功能**: 单向视频流传输
- **数据**: 视频帧、检测框

**两个插件独立运行，互不干扰！**

## 版本历史

- v2.0.0 (2025-10-10)
  - 拆分为独立的指令流插件
  - 专注于WebSocket双向通信
  - 移除视频流相关代码
  - 优化连接管理
  - 简化代码结构