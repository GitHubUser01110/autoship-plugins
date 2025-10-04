"""
设备统一控制插件 - plugin.py

功能：
- 订阅船只控制命令事件
- 将控制命令转换为设备命令事件并发布
- 订阅设备反馈事件（命令结果、状态、报警）
- 提供设备控制接口
- 维护设备状态缓存
"""

import time
import threading
from typing import Any, Dict, Optional
from app.usv.plugin_base import Plugin, Response, PluginState
from app.usv.event_bus import EventData, EventPriority


class DeviceControlPlugin(Plugin):
    """设备统一控制插件"""
    
    VERSION = '1.0.0'
    MIN_COMPATIBLE_VERSION = '1.0.0'
    
    def __init__(self, plugin_id: str, plugin_manager):
        super().__init__(plugin_id, plugin_manager)
        
        # 配置参数
        self._target_device = "usb2xxx_1"
        self._command_timeout = 8.0
        self._status_update_interval = 1.0
        
        # 设备状态缓存
        self._device_status: Dict[str, Dict[str, Any]] = {}
        self._status_lock = threading.Lock()
        
        # 最后一次状态更新时间
        self._last_status_update = 0.0
        
        # 订阅者ID列表
        self._event_subscribers = []
        
        self.log_info("设备统一控制插件初始化完成")
    
    # ==================== 插件生命周期方法 ====================
    
    def _handle_install(self) -> Response:
        """安装插件"""
        self.log_info("正在安装设备统一控制插件...")
        
        # 加载配置
        self._target_device = self.get_config('target_device', 'usb2xxx_1')
        self._command_timeout = self.get_config('command_timeout', 8.0)
        self._status_update_interval = self.get_config('status_update_interval', 1.0)
        
        self.log_info(f"配置已加载: target_device={self._target_device}")
        self.log_info("设备统一控制插件安装成功")
        return Response(success=True, data="安装成功")
    
    def _handle_enable(self) -> Response:
        """启用插件"""
        self.log_info("正在启用设备统一控制插件...")
        
        try:
            # 订阅事件
            self._subscribe_events()
            
            self.log_info("设备统一控制插件启用成功")
            return Response(success=True, data="启用成功")
            
        except Exception as e:
            self.log_error(f"启用插件失败: {e}")
            return Response(success=False, data=str(e))
    
    def _handle_disable(self) -> Response:
        """禁用插件"""
        self.log_info("正在禁用设备统一控制插件...")
        
        try:
            # 清理资源
            with self._status_lock:
                self._device_status.clear()
            
            self.log_info("设备统一控制插件禁用成功")
            return Response(success=True, data="禁用成功")
            
        except Exception as e:
            self.log_error(f"禁用插件失败: {e}")
            return Response(success=False, data=str(e))
    
    def _handle_config_update(self, old_config: Dict, new_config: Dict) -> Response:
        """处理配置更新"""
        self.log_info("正在更新配置...")
        
        if 'target_device' in new_config:
            self._target_device = new_config['target_device']
        if 'command_timeout' in new_config:
            self._command_timeout = new_config['command_timeout']
        if 'status_update_interval' in new_config:
            self._status_update_interval = new_config['status_update_interval']
        
        self.log_info("配置更新成功")
        return Response(success=True, data="配置更新成功")
    
    # ==================== 事件订阅 ====================
    
    def _subscribe_events(self):
        """订阅所有需要的事件"""
        # 订阅控制命令事件
        result = self.subscribe_event(
            event_type="ship.control.device_command",
            callback=self._handle_device_command_event,
            priority=EventPriority.HIGH
        )
        if result.success:
            self._event_subscribers.append(result.data['subscriber_id'])
        
        # 订阅设备命令结果事件
        result = self.subscribe_event(
            event_type="device.command.result",
            callback=self._handle_command_result_event,
            priority=EventPriority.NORMAL
        )
        if result.success:
            self._event_subscribers.append(result.data['subscriber_id'])
        
        # 订阅设备状态事件（通配符）
        result = self.subscribe_event(
            event_type="device.status.**",
            callback=self._handle_device_status_event,
            priority=EventPriority.LOW
        )
        if result.success:
            self._event_subscribers.append(result.data['subscriber_id'])
        
        # 订阅报警事件（通配符）
        result = self.subscribe_event(
            event_type="alert.**",
            callback=self._handle_alert_event,
            priority=EventPriority.CRITICAL
        )
        if result.success:
            self._event_subscribers.append(result.data['subscriber_id'])
        
        self.log_info(f"成功订阅 {len(self._event_subscribers)} 个事件")
    
    # ==================== 事件处理器 ====================
    
    def _handle_device_command_event(self, event: EventData):
        """处理设备控制命令事件"""
        try:
            data = event.data
            device_id = data.get('device_id')
            verb = data.get('verb')
            arg = data.get('arg')
            
            self.log_info(f"收到设备命令: {device_id} {verb} {arg}", 
                         event_id=event.event_id, source=event.source)
            
            # 执行命令
            result = self._execute_device_command(device_id, verb, arg)
            
            # 发布命令发送通知
            self.publish_event(
                event_type="device.control.command_sent",
                data={
                    'device_id': device_id,
                    'verb': verb,
                    'arg': arg,
                    'result': result
                },
                correlation_id=event.correlation_id
            )
            
        except Exception as e:
            self.log_error(f"处理设备命令事件失败: {e}", event_id=event.event_id)
    
    def _handle_command_result_event(self, event: EventData):
        """处理命令结果事件"""
        try:
            data = event.data
            device_id = data.get('device_id')
            result = data.get('result', {})
            
            self.log_info(f"收到命令结果: {device_id} -> {result}", 
                         event_id=event.event_id)
            
            # 更新设备状态缓存（如果结果包含状态信息）
            if isinstance(result, dict) and 'data' in result:
                result_data = result['data']
                if isinstance(result_data, dict):
                    with self._status_lock:
                        if device_id not in self._device_status:
                            self._device_status[device_id] = {}
                        self._device_status[device_id].update({
                            'last_command_result': result,
                            'last_update': time.time()
                        })
            
        except Exception as e:
            self.log_error(f"处理命令结果事件失败: {e}", event_id=event.event_id)
    
    def _handle_device_status_event(self, event: EventData):
        """处理设备状态事件"""
        try:
            data = event.data
            device_id = data.get('device_id')
            status = data.get('status', {})
            
            self.log_debug(f"收到设备状态: {device_id}", event_id=event.event_id)
            
            # 更新状态缓存
            with self._status_lock:
                self._device_status[device_id] = {
                    'status': status,
                    'source': data.get('source'),
                    'last_update': time.time()
                }
            
            # 定期发布状态更新汇总
            now = time.time()
            if now - self._last_status_update >= self._status_update_interval:
                self._last_status_update = now
                self.publish_event(
                    event_type="device.control.status_update",
                    data={'all_devices': self._device_status.copy()},
                    priority=EventPriority.LOW
                )
            
        except Exception as e:
            self.log_error(f"处理设备状态事件失败: {e}", event_id=event.event_id)
    
    def _handle_alert_event(self, event: EventData):
        """处理报警事件"""
        try:
            data = event.data
            self.log_warning(f"收到报警事件: {event.event_type} -> {data}", 
                           event_id=event.event_id)
            
            # 可以在这里添加报警响应逻辑
            # 例如：自动停止相关设备、记录日志等
            
        except Exception as e:
            self.log_error(f"处理报警事件失败: {e}", event_id=event.event_id)
    
    # ==================== 设备控制核心逻辑 ====================
    
    def _execute_device_command(self, device_id: str, verb: str, arg: Optional[float]) -> Dict[str, Any]:
        """
        执行设备控制命令
        
        Args:
            device_id: 设备ID (如 pump_0, propeller_0)
            verb: 动作动词 (如 on, off, speed, fwd, back)
            arg: 可选参数 (如速度值)
            
        Returns:
            执行结果字典
        """
        try:
            # 识别设备类型
            device_type = self._get_device_type(device_id)
            if device_type == "unknown":
                return {
                    'success': False,
                    'message': f'未知设备类型: {device_id}'
                }
            
            # 映射动作到命令
            action, value = self._map_action(device_type, verb, arg)
            
            # 发布设备命令事件到event_bus
            event_topic = f"device.command.{device_type}"
            payload = {
                "target_device": self._target_device,
                "device_id": device_id,
                "action": action,
                "value": value
            }
            
            self.publish_event(
                event_type=event_topic,
                data=payload,
                priority=EventPriority.HIGH
            )
            
            self.log_info(f"已发布设备命令事件: {event_topic} -> {payload}")
            
            return {
                'success': True,
                'message': f'命令已发送: {device_id}.{verb}',
                'event_topic': event_topic,
                'payload': payload
            }
            
        except Exception as e:
            self.log_error(f"执行设备命令失败: {e}")
            return {
                'success': False,
                'message': str(e)
            }
    
    def _get_device_type(self, device_id: str) -> str:
        """
        根据设备ID前缀识别设备类型
        
        Args:
            device_id: 设备ID
            
        Returns:
            设备类型字符串
        """
        if device_id.startswith("propeller"):
            return "propeller"
        if device_id.startswith("pump"):
            return "pump"
        if device_id.startswith("cutter"):
            return "cutter"
        if device_id.startswith("conveyor"):
            return "conveyor"
        if device_id.startswith("stepper"):
            return "stepper"
        return "unknown"
    
    def _map_action(self, device_type: str, verb: str, arg: Optional[float]) -> tuple:
        """
        将动词映射为设备动作和值
        
        Args:
            device_type: 设备类型
            verb: 动作动词
            arg: 可选参数
            
        Returns:
            (action, value) 元组
        """
        # 泵设备
        if device_type == "pump":
            if verb in ("on", "start", "turn_on"):
                return "on", None
            if verb in ("off", "stop", "turn_off"):
                return "off", None
        
        # 推进器
        if device_type == "propeller":
            if verb in ("speed", "set_speed"):
                return "set_speed", float(arg) if arg is not None else 0.0
        
        # 切割器
        if device_type == "cutter":
            if verb in ("fwd", "forward"):
                return "on_fwd", None
            if verb in ("back", "backward", "rev"):
                return "on_rev", None
            if verb == "stop":
                return "off", None
        
        # 传送带
        if device_type == "conveyor":
            if verb in ("start", "on", "turn_on"):
                return "on", None
            if verb in ("stop", "off", "turn_off"):
                return "off", None
        
        # 步进电机
        if device_type == "stepper":
            if verb in ("fwd", "forward"):
                return "start_forward", float(arg) if arg is not None else 10.0
            if verb in ("back", "backward", "rev"):
                return "start_backward", float(arg) if arg is not None else 10.0
            if verb == "stop":
                return "stop", None
            if verb == "speed":
                return "set_speed", float(arg) if arg is not None else 10.0
        
        # 默认返回原始动词
        return verb, arg
    
    # ==================== 插件接口方法 ====================
    
    def send_device_command(self, input_data: Any) -> Dict[str, Any]:
        """
        发送设备控制命令（插件接口）
        
        Args:
            input_data: {"device_id": str, "verb": str, "arg": float (optional)}
            
        Returns:
            执行结果
        """
        if not isinstance(input_data, dict):
            return {
                'success': False,
                'message': '输入数据必须是字典格式'
            }
        
        device_id = input_data.get('device_id')
        verb = input_data.get('verb')
        arg = input_data.get('arg')
        
        if not device_id or not verb:
            return {
                'success': False,
                'message': '缺少必要参数: device_id 和 verb'
            }
        
        return self._execute_device_command(device_id, verb, arg)
    
    def get_device_status(self, input_data: Any = None) -> Dict[str, Any]:
        """
        获取设备状态（插件接口）
        
        Args:
            input_data: {"device_id": str (optional)}
            
        Returns:
            设备状态字典
        """
        with self._status_lock:
            if input_data and isinstance(input_data, dict):
                device_id = input_data.get('device_id')
                if device_id:
                    return {
                        'device_id': device_id,
                        'status': self._device_status.get(device_id, {})
                    }
            
            # 返回所有设备状态
            return {
                'all_devices': self._device_status.copy(),
                'device_count': len(self._device_status),
                'last_update': self._last_status_update
            }
    
    def emergency_stop_device(self, input_data: Any) -> Dict[str, Any]:
        """
        紧急停止设备（插件接口）
        
        Args:
            input_data: {"device_id": str}
            
        Returns:
            执行结果
        """
        if not isinstance(input_data, dict):
            return {
                'success': False,
                'message': '输入数据必须是字典格式'
            }
        
        device_id = input_data.get('device_id')
        if not device_id:
            return {
                'success': False,
                'message': '缺少必要参数: device_id'
            }
        
        # 根据设备类型执行紧急停止
        device_type = self._get_device_type(device_id)
        
        if device_type in ("pump", "cutter", "conveyor"):
            return self._execute_device_command(device_id, "off", None)
        elif device_type == "propeller":
            return self._execute_device_command(device_id, "speed", 0.0)
        elif device_type == "stepper":
            return self._execute_device_command(device_id, "stop", None)
        else:
            return {
                'success': False,
                'message': f'不支持的设备类型: {device_type}'
            }
    
    # ==================== 状态保存与恢复 ====================
    
    def _save_custom_state(self) -> Optional[Dict[str, Any]]:
        """保存自定义状态"""
        with self._status_lock:
            return {
                'device_status': self._device_status.copy(),
                'target_device': self._target_device,
                'last_status_update': self._last_status_update,
                'event_subscribers': self._event_subscribers.copy()
            }
    
    def _restore_custom_state(self, custom_state: Dict[str, Any]) -> None:
        """恢复自定义状态"""
        with self._status_lock:
            if 'device_status' in custom_state:
                self._device_status = custom_state['device_status']
            if 'target_device' in custom_state:
                self._target_device = custom_state['target_device']
            if 'last_status_update' in custom_state:
                self._last_status_update = custom_state['last_status_update']
            if 'event_subscribers' in custom_state:
                self._event_subscribers = custom_state['event_subscribers']


# 插件类导出
__plugin_class__ = DeviceControlPlugin