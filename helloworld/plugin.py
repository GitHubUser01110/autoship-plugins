"""
HelloWorld 测试插件

这是一个简单的测试插件，会定期打印 Hello World 消息和当前配置信息
用于验证插件系统的基本功能
"""

import time
import threading
from typing import Dict, Any, Optional
from plugin_base import Plugin, Response


class HelloWorldPlugin(Plugin):
    """
    HelloWorld 测试插件
    
    功能：
    1. 定期打印 Hello World 消息
    2. 显示当前配置信息
    3. 支持配置更新
    4. 支持热插拔功能
    """
    
    # 插件版本信息
    VERSION = '1.0.0'
    MIN_COMPATIBLE_VERSION = '1.0.0'
    
    def __init__(self, plugin_id: str, plugin_base):
        """初始化插件"""
        super().__init__(plugin_id, plugin_base)
        
        # 插件特有状态
        self._print_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._message_count = 0
        
        # 默认配置
        self.default_config = {
            'print_interval': 5.0,      # 打印间隔（秒）
            'message_prefix': 'Hello',   # 消息前缀
            'message_suffix': 'World!',  # 消息后缀
            'show_timestamp': True,      # 是否显示时间戳
            'show_config': True,         # 是否显示配置
            'max_messages': 0            # 最大消息数（0为无限制）
        }
        
        # 设置状态架构
        self._state_schema = {
            "type": "object",
            "properties": {
                "message_count": {"type": "integer"},
                "start_time": {"type": "number"},
                "last_message_time": {"type": "number"}
            }
        }
        
        self.log_info("HelloWorld 插件初始化完成")
    
    # ==================== 生命周期方法 ====================
    
    def _handle_install(self) -> Response:
        """处理安装逻辑"""
        try:
            # 创建默认配置文件
            if not self.config:
                self.config = self.default_config.copy()
            
            self.log_info("HelloWorld 插件安装完成", operation="install")
            return Response(success=True, data="HelloWorld 插件安装成功")
        except Exception as e:
            self.log_error("安装失败", error=str(e), operation="install")
            return Response(success=False, data=str(e))
    
    def _handle_uninstall(self) -> Response:
        """处理卸载逻辑"""
        try:
            # 停止打印线程
            self._stop_printing()
            
            self.log_info("HelloWorld 插件卸载完成", operation="uninstall")
            return Response(success=True, data="HelloWorld 插件卸载成功")
        except Exception as e:
            self.log_error("卸载失败", error=str(e), operation="uninstall")
            return Response(success=False, data=str(e))
    
    def _handle_enable(self) -> Response:
        """处理启用逻辑"""
        try:
            # 合并默认配置
            merged_config = self.default_config.copy()
            merged_config.update(self.config)
            self.config = merged_config
            
            # 初始化状态
            if not self.get_persistent_state('start_time'):
                self.set_persistent_state('start_time', time.time())
            
            # 开始打印线程
            self._start_printing()
            
            # 发布启用事件
            self.publish_event("helloworld.enabled", {
                "plugin_id": self.plugin_id,
                "config": self.config
            })
            
            self.log_info("HelloWorld 插件启用成功", operation="enable")
            return Response(success=True, data="HelloWorld 插件启用成功")
        except Exception as e:
            self.log_error("启用失败", error=str(e), operation="enable")
            return Response(success=False, data=str(e))
    
    def _handle_disable(self) -> Response:
        """处理禁用逻辑"""
        try:
            # 停止打印线程
            self._stop_printing()
            
            # 发布禁用事件
            self.publish_event("helloworld.disabled", {
                "plugin_id": self.plugin_id,
                "total_messages": self._message_count
            })
            
            self.log_info("HelloWorld 插件禁用成功", 
                         operation="disable", 
                         total_messages=self._message_count)
            return Response(success=True, data="HelloWorld 插件禁用成功")
        except Exception as e:
            self.log_error("禁用失败", error=str(e), operation="disable")
            return Response(success=False, data=str(e))
    
    def _handle_config_update(self, old_config: Dict, new_config: Dict) -> Response:
        """处理配置更新"""
        try:
            # 验证配置
            validation_result = self._validate_config(new_config)
            if not validation_result.success:
                return validation_result
            
            # 如果间隔时间改变了，重启打印线程
            old_interval = old_config.get('print_interval', 5.0)
            new_interval = new_config.get('print_interval', 5.0)
            
            if old_interval != new_interval and self.is_enabled:
                self.log_info("打印间隔改变，重启打印线程",
                             old_interval=old_interval,
                             new_interval=new_interval)
                self._stop_printing()
                self._start_printing()
            
            # 发布配置更新事件
            self.publish_event("helloworld.config_updated", {
                "plugin_id": self.plugin_id,
                "old_config": old_config,
                "new_config": new_config
            })
            
            self.log_info("配置更新成功", operation="config_update")
            return Response(success=True, data="配置更新成功")
        except Exception as e:
            self.log_error("配置更新失败", error=str(e), operation="config_update")
            return Response(success=False, data=str(e))
    
    # ==================== 状态管理 ====================
    
    def _save_custom_state(self) -> Optional[Dict[str, Any]]:
        """保存自定义状态"""
        return {
            'message_count': self._message_count,
            'start_time': self.get_persistent_state('start_time'),
            'last_message_time': self.get_persistent_state('last_message_time'),
            'thread_active': self._print_thread is not None and self._print_thread.is_alive()
        }
    
    def _restore_custom_state(self, custom_state: Dict[str, Any]) -> None:
        """恢复自定义状态"""
        self._message_count = custom_state.get('message_count', 0)
        
        # 恢复持久化状态
        if 'start_time' in custom_state:
            self.set_persistent_state('start_time', custom_state['start_time'])
        if 'last_message_time' in custom_state:
            self.set_persistent_state('last_message_time', custom_state['last_message_time'])
        
        # 如果之前线程是活动的且插件已启用，重新启动打印线程
        was_active = custom_state.get('thread_active', False)
        if was_active and self.is_enabled:
            self._start_printing()
        
        self.log_info("状态恢复完成", 
                     restored_message_count=self._message_count,
                     thread_restarted=was_active)
    
    # ==================== 插件接口 ====================
    
    def get_status(self, input_data: Any = None) -> Dict[str, Any]:
        """获取插件状态接口"""
        return {
            'enabled': self.is_enabled,
            'message_count': self._message_count,
            'start_time': self.get_persistent_state('start_time'),
            'last_message_time': self.get_persistent_state('last_message_time'),
            'uptime_seconds': time.time() - (self.get_persistent_state('start_time') or time.time()),
            'thread_active': self._print_thread is not None and self._print_thread.is_alive(),
            'config': self.config
        }
    
    def send_custom_message(self, input_data: Any = None) -> str:
        """发送自定义消息接口"""
        if isinstance(input_data, dict):
            message = input_data.get('message', 'Custom Hello World!')
        elif isinstance(input_data, str):
            message = input_data
        else:
            message = 'Custom Hello World!'
        
        self._print_message(message, is_custom=True)
        return f"已发送自定义消息: {message}"
    
    def reset_counter(self, input_data: Any = None) -> str:
        """重置消息计数器接口"""
        old_count = self._message_count
        self._message_count = 0
        self.set_persistent_state('start_time', time.time())
        
        self.log_info("消息计数器已重置", old_count=old_count)
        return f"计数器已重置 (之前: {old_count})"
    
    # ==================== 私有方法 ====================
    
    def _validate_config(self, config: Dict[str, Any]) -> Response:
        """验证配置"""
        try:
            # 检查必需的配置项
            interval = config.get('print_interval', 5.0)
            if not isinstance(interval, (int, float)) or interval <= 0:
                return Response(success=False, data="print_interval 必须是正数")
            
            max_messages = config.get('max_messages', 0)
            if not isinstance(max_messages, int) or max_messages < 0:
                return Response(success=False, data="max_messages 必须是非负整数")
            
            return Response(success=True)
        except Exception as e:
            return Response(success=False, data=f"配置验证失败: {str(e)}")
    
    def _start_printing(self):
        """开始打印线程"""
        if self._print_thread and self._print_thread.is_alive():
            return
        
        self._stop_event.clear()
        self._print_thread = threading.Thread(
            target=self._print_loop,
            name=f"HelloWorld-{self.plugin_id}",
            daemon=True
        )
        self._print_thread.start()
        
        self.log_info("打印线程已启动")
    
    def _stop_printing(self):
        """停止打印线程"""
        if self._stop_event:
            self._stop_event.set()
        
        if self._print_thread and self._print_thread.is_alive():
            self._print_thread.join(timeout=2.0)
            if self._print_thread.is_alive():
                self.log_warning("打印线程未能正常停止")
        
        self.log_info("打印线程已停止")
    
    def _print_loop(self):
        """打印循环"""
        try:
            while not self._stop_event.is_set() and self.is_enabled:
                # 检查最大消息数限制
                max_messages = self.get_config('max_messages', 0)
                if max_messages > 0 and self._message_count >= max_messages:
                    self.log_info("达到最大消息数限制，停止打印", max_messages=max_messages)
                    break
                
                # 构造并打印消息
                message = self._build_message()
                self._print_message(message)
                
                # 等待下一次打印
                interval = self.get_config('print_interval', 5.0)
                if self._stop_event.wait(timeout=interval):
                    break
                    
        except Exception as e:
            self.log_error("打印循环异常", error=str(e))
        finally:
            self.log_info("打印循环结束")
    
    def _build_message(self) -> str:
        """构造消息"""
        prefix = self.get_config('message_prefix', 'Hello')
        suffix = self.get_config('message_suffix', 'World!')
        show_timestamp = self.get_config('show_timestamp', True)
        show_config = self.get_config('show_config', True)
        
        # 基础消息
        message_parts = [f"{prefix} {suffix}"]
        
        # 添加计数
        message_parts.append(f"(#{self._message_count + 1})")
        
        # 添加时间戳
        if show_timestamp:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            message_parts.append(f"[{timestamp}]")
        
        base_message = " ".join(message_parts)
        
        # 添加配置信息
        if show_config:
            config_info = [
                f"间隔: {self.get_config('print_interval')}s",
                f"最大消息: {self.get_config('max_messages') or '无限制'}"
            ]
            config_str = ", ".join(config_info)
            return f"{base_message} | 配置: {config_str}"
        
        return base_message
    
    def _print_message(self, message: str, is_custom: bool = False):
        """打印消息"""
        try:
            # 打印到日志
            self.log_info(message, 
                         message_count=self._message_count + 1,
                         is_custom=is_custom)
            
            # 也可以打印到控制台
            print(f"[{self.plugin_id}] {message}")
            
            # 更新计数和时间戳
            if not is_custom:
                self._message_count += 1
            self.set_persistent_state('last_message_time', time.time())
            
            # 发布消息事件
            self.publish_event("helloworld.message_sent", {
                "plugin_id": self.plugin_id,
                "message": message,
                "message_count": self._message_count,
                "is_custom": is_custom,
                "timestamp": time.time()
            })
            
        except Exception as e:
            self.log_error("消息打印失败", error=str(e))