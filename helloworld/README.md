# HelloWorld 测试插件

一个简单的测试插件，用于验证无人船插件系统的基本功能。

## 功能特性

- ✅ 定期打印 Hello World 消息
- ✅ 显示当前配置信息
- ✅ 支持配置热更新
- ✅ 支持热插拔功能
- ✅ 提供多个接口调用
- ✅ 发布事件通知
- ✅ 状态持久化

## 配置参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `print_interval` | number | 5.0 | 消息打印间隔时间（秒） |
| `message_prefix` | string | "Hello" | 消息前缀 |
| `message_suffix` | string | "World!" | 消息后缀 |
| `show_timestamp` | boolean | true | 是否显示时间戳 |
| `show_config` | boolean | true | 是否显示配置信息 |
| `max_messages` | integer | 0 | 最大消息数（0=无限制） |

## 提供的接口

### 1. get_status
获取插件当前状态信息

```python
# 调用示例
status = plugin_manager.call_plugin_interface("helloworld", "get_status")
print(status.data)
```

### 2. send_custom_message
发送自定义消息

```python
# 字符串方式
result = plugin_manager.call_plugin_interface("helloworld", "send_custom_message", "自定义消息")

# 字典方式
result = plugin_manager.call_plugin_interface("helloworld", "send_custom_message", {
    "message": "这是一条自定义消息"
})
```

### 3. reset_counter
重置消息计数器

```python
result = plugin_manager.call_plugin_interface("helloworld", "reset_counter")
```

## 发布的事件

- `helloworld.enabled` - 插件启用时触发
- `helloworld.disabled` - 插件禁用时触发
- `helloworld.config_updated` - 配置更新时触发
- `helloworld.message_sent` - 发送消息时触发

## 安装和使用

### 1. 安装插件

```python
# 发现插件
plugins = plugin_manager.discover_plugins()

# 安装插件
result = plugin_manager.install_plugin("helloworld")
if result.success:
    print("安装成功")
```

### 2. 启用插件

```python
result = plugin_manager.enable_plugin("helloworld")
if result.success:
    print("启用成功，开始打印消息")
```

### 3. 更新配置

```python
new_config = {
    "print_interval": 2.0,
    "message_prefix": "Hi",
    "message_suffix": "Universe!",
    "show_timestamp": True,
    "show_config": False,
    "max_messages": 10
}

result = plugin_manager.update_plugin_config("helloworld", new_config)
if result.success:
    print("配置更新成功")
```

### 4. 热升级测试

```python
# 热升级插件
result = plugin_manager.hot_upgrade_plugin("helloworld", target_version="1.1.0")
if result.success:
    task_id = result.data['task_id']
    print(f"热升级任务已启动: {task_id}")
    
    # 查看任务状态
    status = plugin_manager.get_hotswap_task_status(task_id)
    print(status.data)
```

### 5. 禁用和卸载

```python
# 禁用插件
plugin_manager.disable_plugin("helloworld")

# 卸载插件
plugin_manager.uninstall_plugin("helloworld")
```

## 目录结构

```
helloworld/
├── manifest.json    # 插件清单文件
├── plugin.py        # 插件主代码
├── config.json      # 默认配置文件
└── README.md        # 说明文档
```

## 预期输出示例

当插件启用后，会看到类似以下的输出：

```
[helloworld] Hello World! (#1) [2025-01-01 12:00:00] | 配置: 间隔: 5.0s, 最大消息: 无限制
[helloworld] Hello World! (#2) [2025-01-01 12:00:05] | 配置: 间隔: 5.0s, 最大消息: 无限制
[helloworld] Hello World! (#3) [2025-01-01 12:00:10] | 配置: 间隔: 5.0s, 最大消息: 无限制
...
```

## 测试场景

这个插件可以用来测试以下功能：

1. **基础功能测试**
   - 插件安装、启用、禁用、卸载
   - 配置加载和更新
   - 接口调用

2. **热插拔测试**
   - 热升级/降级
   - 热重载
   - 状态保存和恢复

3. **事件系统测试**
   - 事件发布和订阅
   - 事件传播

4. **并发测试**
   - 多插件同时运行
   - 配置并发更新

5. **稳定性测试**
   - 长时间运行
   - 异常恢复

## 版本历史

- **v1.0.0** - 初始版本，包含基础功能