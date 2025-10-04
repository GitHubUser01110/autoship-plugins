# 设备统一控制插件

## 概述
设备统一控制插件提供智能无人船的设备控制功能，支持推进器、泵、切割器、传送带、步进电机等多种设备的统一控制和状态监控。插件通过事件总线与设备通信，完全基于事件驱动架构。

## 支持的设备类型

### 1. 推进器 (Propeller)
- **设备ID格式**: `propeller_0`, `propeller_1`, ...
- **控制动作**:
  - `speed <value>` 或 `set_speed <value>`: 设置速度 (-100 到 100)

### 2. 泵 (Pump)
- **设备ID格式**: `pump_0`, `pump_1`, ...
- **控制动作**:
  - `on` / `start` / `turn_on`: 开启泵
  - `off` / `stop` / `turn_off`: 关闭泵

### 3. 切割器 (Cutter)
- **设备ID格式**: `cutter_0`, `cutter_1`, ...
- **控制动作**:
  - `fwd` / `forward`: 正向旋转
  - `back` / `backward` / `rev`: 反向旋转
  - `stop`: 停止

### 4. 传送带 (Conveyor)
- **设备ID格式**: `conveyor_0`, `conveyor_1`, ...
- **控制动作**:
  - `start` / `on` / `turn_on`: 启动传送带
  - `stop` / `off` / `turn_off`: 停止传送带

### 5. 步进电机 (Stepper)
- **设备ID格式**: `stepper_0`, `stepper_1`, ...
- **控制动作**:
  - `fwd [rpm]` / `forward [rpm]`: 正向运行（可选转速，默认10）
  - `back [rpm]` / `backward [rpm]` / `rev [rpm]`: 反向运行
  - `stop`: 停止
  - `speed <rpm>`: 设置速度

## 订阅事件

### ship.control.device_command
接收设备控制命令
```json
{
    "device_id": "pump_0",
    "verb": "on",
    "arg": null
}
```

### device.command.result
接收设备命令执行结果（由桥接器转发）
```json
{
    "device_id": "pump_0",
    "result": {
        "status": "success",
        "message": "..."
    }
}
```

### device.status.** (通配符)
接收设备状态更新
```json
{
    "device_id": "propeller_0",
    "status": {
        "speed": 50,
        "...": "..."
    },
    "source": "usb2xxx_1"
}
```

### alert.** (通配符)
接收设备报警事件
```json
{
    "level": "critical",
    "message": "设备故障",
    "device_id": "pump_0"
}
```

## 发布事件

### device.command.pump
发送泵控制命令到event_bus（由桥接器转发到设备）
```json
{
    "target_device": "usb2xxx_1",
    "device_id": "pump_0",
    "action": "on",
    "value": null
}
```

### device.command.propeller
发送推进器控制命令
```json
{
    "target_device": "usb2xxx_1",
    "device_id": "propeller_0",
    "action": "set_speed",
    "value": 50.0
}
```

### device.command.cutter
发送切割器控制命令
```json
{
    "target_device": "usb2xxx_1",
    "device_id": "cutter_0",
    "action": "on_fwd",
    "value": null
}
```

### device.command.conveyor
发送传送带控制命令
```json
{
    "target_device": "usb2xxx_1",
    "device_id": "conveyor_0",
    "action": "on",
    "value": null
}
```

### device.command.stepper
发送步进电机控制命令
```json
{
    "target_device": "usb2xxx_1",
    "device_id": "stepper_0",
    "action": "start_forward",
    "value": 10.0
}
```

### device.control.command_sent
命令发送通知
```json
{
    "device_id": "pump_0",
    "verb": "on",
    "arg": null,
    "result": {
        "success": true,
        "message": "命令已发送"
    }
}
```

### device.control.status_update
状态更新汇总（定期发布）
```json
{
    "all_devices": {
        "pump_0": {
            "status": {...},
            "last_update": 1234567890.0
        }
    }
}
```

## 接口方法

### send_device_command(input_data)
发送设备控制命令
- **输入**:
```json
{
    "device_id": "pump_0",
    "verb": "on",
    "arg": null
}
```
- **输出**:
```json
{
    "success": true,
    "message": "命令已发送: pump_0.on",
    "event_topic": "device.command.pump",
    "payload": {...}
}
```

### get_device_status(input_data)
获取设备状态
- **输入**（可选）:
```json
{
    "device_id": "pump_0"
}
```
- **输出**:
```json
{
    "device_id": "pump_0",
    "status": {
        "status": {...},
        "last_update": 1234567890.0
    }
}
```

### emergency_stop_device(input_data)
紧急停止设备
- **输入**:
```json
{
    "device_id": "pump_0"
}
```
- **输出**:
```json
{
    "success": true,
    "message": "命令已发送: pump_0.off"
}
```

## 配置参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| target_device | string | usb2xxx_1 | 目标设备名称（与桥接器配置一致） |
| command_timeout | number | 8.0 | 设备命令超时时间（秒） |
| status_update_interval | number | 1.0 | 状态更新间隔（秒） |

## 使用示例

### 1. 通过事件控制设备
```python
from app.usv.event_bus import event_bus, EventPriority

# 发布设备控制命令
event_bus.publish(
    event_type="ship.control.device_command",
    data={
        "device_id": "pump_0",
        "verb": "on"
    },
    source="control_panel",
    priority=EventPriority.HIGH
)
```

### 2. 通过接口调用
```python
# 发送设备命令
result = plugin_manager.call_plugin_interface(
    "device_control",
    "send_device_command",
    {
        "device_id": "propeller_0",
        "verb": "speed",
        "arg": 50.0
    }
)

# 获取设备状态
status = plugin_manager.call_plugin_interface(
    "device_control",
    "get_device_status",
    {"device_id": "pump_0"}
)

# 紧急停止设备
result = plugin_manager.call_plugin_interface(
    "device_control",
    "emergency_stop_device",
    {"device_id": "cutter_0"}
)
```

### 3. 订阅设备反馈
```python
def on_command_result(event):
    data = event.data
    print(f"设备 {data['device_id']} 命令结果: {data['result']}")

event_bus.subscribe("device.command.result", on_command_result)
```

## 工作流程

1. **命令发送**:
   - 外部系统发布 `ship.control.device_command` 事件
   - 插件接收事件，识别设备类型
   - 插件将命令映射为标准格式
   - 插件发布 `device.command.<type>` 事件到event_bus
   - 桥接器订阅该事件并转发到UDP设备

2. **状态接收**:
   - 设备通过UDP发送状态到桥接器
   - 桥接器发布 `device.status.**` 事件
   - 插件接收并更新状态缓存
   - 定期发布状态更新汇总

3. **命令反馈**:
   - 设备执行命令后通过UDP返回结果
   - 桥接器发布 `device.command.result` 事件
   - 插件接收并记录结果

## 架构说明

```
外部系统
   ↓ (发布 ship.control.device_command)
设备控制插件
   ↓ (发布 device.command.*)
Event Bus
   ↓ (订阅 device.command.*)
事件桥接器
   ↓ (UDP通信)
物理设备
```

## 注意事项

1. **target_device配置**: 必须与桥接器的devices_config中的设备名称一致
2. **事件驱动**: 所有通信都通过事件总线，不直接使用UDP
3. **状态缓存**: 插件会缓存最近的设备状态，可能存在延迟
4. **命令异步**: 命令发送是异步的，通过订阅result事件获取执行结果
5. **热插拔**: 支持运行时启用/禁用，无需重启系统

## 版本历史

- v1.0.0 (2025-10-04)
  - 初始版本
  - 支持推进器、泵、切割器、传送带、步进电机控制
  - 基于事件总线的完全异步架构
  - 设备状态缓存和监控
  - 紧急停止功能