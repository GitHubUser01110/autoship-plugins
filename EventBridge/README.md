# 事件桥接器插件

## 概述
事件桥接器插件是智能无人船系统的核心基础设施组件，负责event_bus事件系统与UDP物理设备之间的双向消息转换和路由。作为系统级插件，它为所有设备控制插件提供底层通信能力。

## 核心功能

### 1. 下行通信（Event → UDP）
- 订阅 `device.command.**` 事件
- 将事件数据转换为UDP消息格式
- 根据target_device路由到对应设备
- 自动生成消息序列号

### 2. 上行通信（UDP → Event）
- 监听UDP端口接收设备上报
- 解析UDP消息并转换为事件
- 发布设备响应、状态、传感器数据、报警等事件
- 自动学习设备地址

### 3. 设备路由
- 支持多设备配置
- 动态设备地址学习
- 设备地址缓存

### 4. 心跳机制
- 接收设备心跳上报
- 自动发送心跳应答
- 发布心跳事件

## 架构位置

```
应用层插件（导航、控制等）
    ↓ 发布 device.command.* 事件
Event Bus（事件总线）
    ↓ 订阅/发布
事件桥接器插件（本插件）
    ↓ UDP通信
物理设备（Jetson等）
```

## 订阅事件

### device.command.** (通配符)
接收所有设备命令事件并转发到UDP

**事件数据格式**:
```json
{
    "target_device": "usb2xxx_1",
    "device_id": "pump_0",
    "action": "on",
    "value": null
}
```

**转换为UDP消息**:
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
    "source": "usb2xxx_1",
    "addr": "192.168.69.67:12345"
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
    "source": "usb2xxx_1",
    "addr": "192.168.69.67:12345"
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
    "learned_devices": ["usb2xxx_1"],
    "statistics": {
        "uptime": 3600.5,
        "messages_sent": 1234,
        "messages_received": 5678,
        "commands_forwarded": 456,
        "errors": 2
    }
}
```

### get_device_address(device_name)
获取设备实际地址（从学习缓存中）

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
    "host": "192.168.69.67",
    "port": 12345
}
```

### send_raw_udp(host, port, message)
发送原始UDP消息（调试用）

**输入**:
```json
{
    "host": "192.168.69.67",
    "port": 12345,
    "message": {
        "type": "test",
        "data": "hello"
    }
}
```

**输出**:
```json
{
    "success": true,
    "message": "消息已发送到 192.168.69.67:12345"
}
```

## 配置参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| listen_port | number | 13000 | UDP监听端口 |
| devices | object | {...} | 设备路由配置表 |
| socket_timeout | number | 1.0 | Socket超时时间（秒） |
| recv_buffer_size | number | 4096 | 接收缓冲区大小（字节） |

### devices 配置格式

```json
{
    "usb2xxx_1": {
        "host": "192.168.69.67",
        "port": 12345
    },
    "usb2xxx_2": {
        "host": "192.168.69.68",
        "port": 12345
    }
}
```

## 消息转换映射

### 动作到命令映射

| Action | UDP Command |
|--------|-------------|
| on | `{"state": true}` |
| off | `{"state": false}` |
| set_speed | `{"speed": value}` |
| on_fwd | `{"state": true, "direction": true}` |
| on_rev | `{"state": true, "direction": false}` |
| start_forward | `{"action": "start_forward", "speed": value}` |
| start_backward | `{"action": "start_backward", "speed": value}` |
| stop | `{"action": "stop"}` |

## 使用示例

### 1. 安装和启动
```python
# 安装插件
plugin_manager.install_plugin("event_bridge")

# 启用插件（会自动启动UDP服务器）
plugin_manager.enable_plugin("event_bridge")
```

### 2. 获取状态
```python
status = plugin_manager.call_plugin_interface(
    "event_bridge",
    "get_bridge_status"
)
print(f"桥接器运行状态: {status}")
```

### 3. 查询设备地址
```python
addr = plugin_manager.call_plugin_interface(
    "event_bridge",
    "get_device_address",
    {"device_name": "usb2xxx_1"}
)
print(f"设备地址: {addr['host']}:{addr['port']}")
```

### 4. 订阅设备反馈
```python
from event_bus import event_bus

def on_command_result(event):
    data = event.data
    print(f"设备 {data['device_id']} 命令结果: {data['result']}")

event_bus.subscribe("device.command.result", on_command_result)
```

## 工作流程

### 命令下发流程
1. 应用层插件发布 `device.command.*` 事件
2. 桥接器订阅并接收事件
3. 验证target_device是否在配置中
4. 构建UDP命令消息
5. 根据设备配置发送UDP消息到对应host:port
6. 更新统计信息

### 状态上报流程
1. 物理设备通过UDP发送消息到桥接器监听端口
2. 桥接器接收并解析UDP消息
3. 学习并缓存设备地址
4. 根据消息类型转换为对应事件
5. 发布事件到event_bus
6. 更新统计信息

### 心跳处理流程
1. 设备发送heartbeat_report到桥接器
2. 桥接器发布system.heartbeat事件
3. 桥接器自动回复heartbeat_ack
4. 保持连接活跃

## 统计信息

桥接器实时统计以下信息：
- **uptime**: 运行时长（秒）
- **messages_sent**: 已发送UDP消息数
- **messages_received**: 已接收UDP消息数
- **commands_forwarded**: 已转发命令数
- **errors**: 错误次数

## 错误处理

### 常见错误

1. **端口被占用**
   - 错误: `[Errno 48] Address already in use`
   - 解决: 检查端口是否被其他程序占用，或修改listen_port配置

2. **设备未配置**
   - 错误: `未知设备: xxx`
   - 解决: 在devices配置中添加设备路由信息

3. **JSON解析失败**
   - 错误: `JSON解析失败`
   - 解决: 检查UDP消息格式是否正确

4. **Socket未初始化**
   - 错误: `UDP Socket未初始化`
   - 解决: 确保插件已正确启用

### 错误统计

所有错误都会被记录到统计信息中，可以通过 `get_bridge_status()` 查询。

## 依赖关系

### 本插件被依赖于
- 设备统一控制插件 (DeviceControl)
- 其他需要与物理设备通信的插件

### 本插件依赖于
- event_bus (事件总线系统)
- Python标准库: socket, json, threading

## 性能特性

1. **异步处理**: 上行消息在独立线程中处理，不阻塞接收循环
2. **地址缓存**: 自动学习设备地址，减少配置复杂度
3. **线程安全**: 使用锁保护共享数据
4. **资源清理**: 插件禁用时自动清理所有资源

## 注意事项

1. **端口配置**: listen_port和设备port不能冲突
2. **网络要求**: 确保网络可达，防火墙允许UDP通信
3. **配置更新**: 设备列表可热更新，端口变更需重启插件
4. **系统级插件**: 建议在所有设备控制插件之前启动
5. **单例运行**: 同一listen_port只能有一个桥接器实例

## 调试技巧

### 1. 查看统计信息
```python
status = plugin_manager.call_plugin_interface("event_bridge", "get_bridge_status")
print(json.dumps(status, indent=2))
```

### 2. 发送测试消息
```python
plugin_manager.call_plugin_interface(
    "event_bridge",
    "send_raw_udp",
    {
        "host": "192.168.69.67",
        "port": 12345,
        "message": {"type": "test", "data": "ping"}
    }
)
```

### 3. 监控日志
插件会输出详细日志，包括：
- 收到的命令事件
- 发送的UDP消息
- 接收的UDP消息
- 转发的事件
- 错误信息

## 版本历史

- v1.0.0 (2025-10-04)
  - 初始版本
  - 实现双向事件-UDP转换
  - 支持多设备路由
  - 自动心跳应答
  - 地址学习缓存
  - 完整的统计信息