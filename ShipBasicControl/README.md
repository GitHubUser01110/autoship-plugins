"""
# 船只基础控制插件

## 概述
船只基础控制插件提供智能无人船的核心控制功能，包括速度控制、方向控制、停止和紧急停止等基础操作。

## 功能特性

### 1. 速度控制
- 支持设置目标速度（0 - max_speed m/s）
- 平滑加速/减速，避免突变
- 自动限制在最大速度范围内

### 2. 方向控制
- 支持设置目标方向（0-360度）
- 自动选择最短转向路径
- 限制转向速率，确保平稳转向

### 3. 停止控制
- 普通停止：按照正常减速度停船
- 紧急停止：立即停止所有动力，快速制动

### 4. 状态监控
- 实时监控船只速度、方向
- 监控引擎功率和舵角
- 定期发布状态更新事件

## 订阅事件

### ship.control.set_speed
设置目标速度
```json
{
    "speed": 5.0  // 目标速度 (m/s)
}
```

### ship.control.set_direction
设置目标方向
```json
{
    "direction": 90.0  // 目标方向 (度)
}
```

### ship.control.stop
停止船只（无需参数）

### ship.control.emergency_stop
紧急停止船只（无需参数）

## 发布事件

### ship.control.speed_set_response
速度设置响应
```json
{
    "success": true,
    "target_speed": 5.0,
    "current_speed": 3.2,
    "message": "目标速度设置为 5.0 m/s"
}
```

### ship.control.direction_set_response
方向设置响应
```json
{
    "success": true,
    "target_direction": 90.0,
    "current_direction": 75.0,
    "message": "目标方向设置为 90.0°"
}
```

### ship.control.status_update
状态更新（每秒发布一次）
```json
{
    "speed": 5.0,
    "direction": 90.0,
    "target_speed": 5.0,
    "target_direction": 90.0,
    "engine_power": 50.0,
    "rudder_angle": 0.0,
    "status": "moving",
    "last_update": 1695945600.0
}
```

## 接口方法

### get_ship_status()
获取船只当前状态
- 输入：无
- 输出：包含所有状态信息的字典

### set_speed(speed)
设置目标速度
- 输入：`{"speed": 5.0}`
- 输出：`{"success": true, "message": "..."}`

### set_direction(direction)
设置目标方向
- 输入：`{"direction": 90.0}`
- 输出：`{"success": true, "message": "..."}`

### stop()
停止船只
- 输入：无
- 输出：`{"success": true, "message": "..."}`

### emergency_stop()
紧急停止
- 输入：无
- 输出：`{"success": true, "message": "..."}`

## 配置参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| max_speed | number | 10.0 | 最大速度 (m/s) |
| max_acceleration | number | 2.0 | 最大加速度 (m/s²) |
| max_turn_rate | number | 30.0 | 最大转向速率 (度/秒) |
| emergency_decel | number | 5.0 | 紧急制动减速度 (m/s²) |
| control_interval | number | 0.1 | 控制周期 (秒) |

## 使用示例

### 1. 通过事件控制
```python
# 发布速度控制事件
event_bus.publish(
    event_type="ship.control.set_speed",
    data={"speed": 5.0},
    source="navigation_system"
)

# 发布方向控制事件
event_bus.publish(
    event_type="ship.control.set_direction",
    data={"direction": 90.0},
    source="navigation_system"
)

# 紧急停止
event_bus.publish(
    event_type="ship.control.emergency_stop",
    source="safety_system",
    priority=EventPriority.EMERGENCY
)
```

### 2. 通过接口调用
```python
# 获取状态
result = plugin_manager.call_plugin_interface(
    "ship_basic_control",
    "get_ship_status"
)

# 设置速度
result = plugin_manager.call_plugin_interface(
    "ship_basic_control",
    "set_speed",
    {"speed": 5.0}
)
```

## 安全特性

1. **速度限制**：自动限制速度在配置的安全范围内
2. **平滑控制**：避免突然的速度和方向变化
3. **紧急停止**：优先级最高的紧急制动功能
4. **状态监控**：实时监控并发布船只状态

## 注意事项

1. 插件禁用时会自动执行紧急停止
2. 配置更新后立即生效，无需重启
3. 所有控制操作都是异步的，通过事件系统通信
4. 支持热插拔，可以在运行时升级或重载

## 版本历史

- v1.0.0 (2025-09-29)
  - 初始版本
  - 实现基础速度和方向控制
  - 支持普通停止和紧急停止
  - 实时状态监控和发布
"""