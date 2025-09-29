"""
船只基础控制插件 - plugin.py

功能：
- 订阅船只控制相关事件
- 执行基础的船只控制操作（速度、方向、停止等）
- 提供船只状态查询接口
- 支持紧急停止功能
"""

import time
import threading
from typing import Any, Dict, Optional
from plugin_base import Plugin, Response, PluginState
from event_bus import EventData, EventPriority


class ShipBasicControlPlugin(Plugin):
    """船只基础控制插件"""
    
    VERSION = '1.0.0'
    MIN_COMPATIBLE_VERSION = '1.0.0'
    
    def __init__(self, plugin_id: str, plugin_manager):
        super().__init__(plugin_id, plugin_manager)
        
        # 船只状态
        self._ship_state = {
            'speed': 0.0,          # 当前速度 (m/s)
            'direction': 0.0,      # 当前方向 (度, 0-360)
            'target_speed': 0.0,   # 目标速度
            'target_direction': 0.0,  # 目标方向
            'engine_power': 0.0,   # 引擎功率 (0-100%)
            'rudder_angle': 0.0,   # 舵角 (-45 到 45 度)
            'status': 'stopped',   # 状态: stopped, moving, emergency_stop
            'last_update': time.time()
        }
        
        # 控制参数
        self._control_params = {
            'max_speed': 10.0,           # 最大速度 (m/s)
            'max_acceleration': 2.0,     # 最大加速度 (m/s²)
            'max_turn_rate': 30.0,       # 最大转向速率 (度/秒)
            'emergency_decel': 5.0,      # 紧急制动减速度 (m/s²)
            'control_interval': 0.1      # 控制周期 (秒)
        }
        
        # 控制线程
        self._control_thread: Optional[threading.Thread] = None
        self._control_running = False
        self._state_lock = threading.Lock()
        
        # 订阅者ID列表
        self._event_subscribers = []
        
        self.log_info("船只基础控制插件初始化完成")
    
    # ==================== 插件生命周期方法 ====================
    
    def _handle_install(self) -> Response:
        """安装插件"""
        self.log_info("正在安装船只基础控制插件...")
        
        # 验证配置
        max_speed = self.get_config('max_speed', 10.0)
        if max_speed <= 0 or max_speed > 50.0:
            return Response(success=False, data="max_speed 配置无效，应在 0-50 范围内")
        
        self._control_params['max_speed'] = max_speed
        self._control_params['max_acceleration'] = self.get_config('max_acceleration', 2.0)
        self._control_params['max_turn_rate'] = self.get_config('max_turn_rate', 30.0)
        self._control_params['emergency_decel'] = self.get_config('emergency_decel', 5.0)
        self._control_params['control_interval'] = self.get_config('control_interval', 0.1)
        
        self.log_info("船只基础控制插件安装成功")
        return Response(success=True, data="安装成功")
    
    def _handle_enable(self) -> Response:
        """启用插件"""
        self.log_info("正在启用船只基础控制插件...")
        
        try:
            # 订阅船只控制事件
            self._subscribe_control_events()
            
            # 启动控制循环线程
            self._start_control_loop()
            
            # 发布插件启用事件
            self.publish_event(
                event_type="ship.control.plugin_enabled",
                data={'plugin_id': self.plugin_id, 'version': self.version}
            )
            
            self.log_info("船只基础控制插件启用成功")
            return Response(success=True, data="启用成功")
            
        except Exception as e:
            self.log_error(f"启用插件失败: {e}")
            return Response(success=False, data=str(e))
    
    def _handle_disable(self) -> Response:
        """禁用插件"""
        self.log_info("正在禁用船只基础控制插件...")
        
        try:
            # 停止控制循环
            self._stop_control_loop()
            
            # 紧急停船
            self._emergency_stop_internal()
            
            # 发布插件禁用事件
            self.publish_event(
                event_type="ship.control.plugin_disabled",
                data={'plugin_id': self.plugin_id}
            )
            
            self.log_info("船只基础控制插件禁用成功")
            return Response(success=True, data="禁用成功")
            
        except Exception as e:
            self.log_error(f"禁用插件失败: {e}")
            return Response(success=False, data=str(e))
    
    def _handle_config_update(self, old_config: Dict, new_config: Dict) -> Response:
        """处理配置更新"""
        self.log_info("正在更新配置...")
        
        # 更新控制参数
        if 'max_speed' in new_config:
            self._control_params['max_speed'] = new_config['max_speed']
        if 'max_acceleration' in new_config:
            self._control_params['max_acceleration'] = new_config['max_acceleration']
        if 'max_turn_rate' in new_config:
            self._control_params['max_turn_rate'] = new_config['max_turn_rate']
        if 'emergency_decel' in new_config:
            self._control_params['emergency_decel'] = new_config['emergency_decel']
        if 'control_interval' in new_config:
            self._control_params['control_interval'] = new_config['control_interval']
        
        self.log_info("配置更新成功")
        return Response(success=True, data="配置更新成功")
    
    # ==================== 事件订阅 ====================
    
    def _subscribe_control_events(self):
        """订阅控制事件"""
        # 订阅速度控制事件
        result = self.subscribe_event(
            event_type="ship.control.set_speed",
            callback=self._handle_set_speed_event,
            priority=EventPriority.HIGH
        )
        if result.success:
            self._event_subscribers.append(result.data['subscriber_id'])
        
        # 订阅方向控制事件
        result = self.subscribe_event(
            event_type="ship.control.set_direction",
            callback=self._handle_set_direction_event,
            priority=EventPriority.HIGH
        )
        if result.success:
            self._event_subscribers.append(result.data['subscriber_id'])
        
        # 订阅停止事件
        result = self.subscribe_event(
            event_type="ship.control.stop",
            callback=self._handle_stop_event,
            priority=EventPriority.CRITICAL
        )
        if result.success:
            self._event_subscribers.append(result.data['subscriber_id'])
        
        # 订阅紧急停止事件
        result = self.subscribe_event(
            event_type="ship.control.emergency_stop",
            callback=self._handle_emergency_stop_event,
            priority=EventPriority.EMERGENCY
        )
        if result.success:
            self._event_subscribers.append(result.data['subscriber_id'])
        
        # 订阅通配符事件用于监控所有船只控制相关事件
        result = self.subscribe_event(
            event_type="ship.control.**",
            callback=self._handle_control_event_monitor,
            priority=EventPriority.LOW
        )
        if result.success:
            self._event_subscribers.append(result.data['subscriber_id'])
        
        self.log_info(f"成功订阅 {len(self._event_subscribers)} 个事件")
    
    # ==================== 事件处理器 ====================
    
    def _handle_set_speed_event(self, event: EventData):
        """处理设置速度事件"""
        try:
            data = event.data
            target_speed = data.get('speed', 0.0)
            
            self.log_info(f"收到设置速度事件: {target_speed} m/s", 
                         event_id=event.event_id, source=event.source)
            
            # 设置目标速度
            result = self._set_target_speed(target_speed)
            
            # 发布响应事件
            self.publish_event(
                event_type="ship.control.speed_set_response",
                data={
                    'success': result['success'],
                    'target_speed': target_speed,
                    'current_speed': self._ship_state['speed'],
                    'message': result.get('message', '')
                },
                correlation_id=event.correlation_id
            )
            
        except Exception as e:
            self.log_error(f"处理设置速度事件失败: {e}", event_id=event.event_id)
    
    def _handle_set_direction_event(self, event: EventData):
        """处理设置方向事件"""
        try:
            data = event.data
            target_direction = data.get('direction', 0.0)
            
            self.log_info(f"收到设置方向事件: {target_direction}°", 
                         event_id=event.event_id, source=event.source)
            
            # 设置目标方向
            result = self._set_target_direction(target_direction)
            
            # 发布响应事件
            self.publish_event(
                event_type="ship.control.direction_set_response",
                data={
                    'success': result['success'],
                    'target_direction': target_direction,
                    'current_direction': self._ship_state['direction'],
                    'message': result.get('message', '')
                },
                correlation_id=event.correlation_id
            )
            
        except Exception as e:
            self.log_error(f"处理设置方向事件失败: {e}", event_id=event.event_id)
    
    def _handle_stop_event(self, event: EventData):
        """处理停止事件"""
        try:
            self.log_info("收到停止事件", event_id=event.event_id, source=event.source)
            
            # 执行停止
            result = self._stop_ship()
            
            # 发布响应事件
            self.publish_event(
                event_type="ship.control.stop_response",
                data={
                    'success': result['success'],
                    'message': result.get('message', '')
                },
                correlation_id=event.correlation_id,
                priority=EventPriority.HIGH
            )
            
        except Exception as e:
            self.log_error(f"处理停止事件失败: {e}", event_id=event.event_id)
    
    def _handle_emergency_stop_event(self, event: EventData):
        """处理紧急停止事件"""
        try:
            self.log_warning("收到紧急停止事件！", event_id=event.event_id, source=event.source)
            
            # 执行紧急停止
            result = self._emergency_stop_internal()
            
            # 发布响应事件（紧急优先级）
            self.publish_event(
                event_type="ship.control.emergency_stop_response",
                data={
                    'success': result['success'],
                    'message': result.get('message', ''),
                    'stopped_at': time.time()
                },
                correlation_id=event.correlation_id,
                priority=EventPriority.EMERGENCY
            )
            
        except Exception as e:
            self.log_error(f"处理紧急停止事件失败: {e}", event_id=event.event_id)
    
    def _handle_control_event_monitor(self, event: EventData):
        """监控所有控制事件"""
        # 记录所有控制事件用于调试和审计
        self.log_debug(f"监控到控制事件: {event.event_type}", 
                      event_id=event.event_id, source=event.source)
    
    # ==================== 控制逻辑 ====================
    
    def _set_target_speed(self, speed: float) -> Dict[str, Any]:
        """设置目标速度"""
        with self._state_lock:
            # 限制速度范围
            if speed < 0:
                speed = 0
            elif speed > self._control_params['max_speed']:
                speed = self._control_params['max_speed']
            
            self._ship_state['target_speed'] = speed
            
            if speed > 0:
                self._ship_state['status'] = 'moving'
            else:
                self._ship_state['status'] = 'stopped'
            
            self.log_info(f"目标速度已设置为: {speed} m/s")
            
            return {
                'success': True,
                'message': f'目标速度设置为 {speed} m/s'
            }
    
    def _set_target_direction(self, direction: float) -> Dict[str, Any]:
        """设置目标方向"""
        with self._state_lock:
            # 规范化方向到 0-360 度
            direction = direction % 360
            
            self._ship_state['target_direction'] = direction
            
            self.log_info(f"目标方向已设置为: {direction}°")
            
            return {
                'success': True,
                'message': f'目标方向设置为 {direction}°'
            }
    
    def _stop_ship(self) -> Dict[str, Any]:
        """停止船只"""
        with self._state_lock:
            self._ship_state['target_speed'] = 0.0
            self._ship_state['status'] = 'stopped'
            
            self.log_info("船只正在停止...")
            
            return {
                'success': True,
                'message': '船只正在停止'
            }
    
    def _emergency_stop_internal(self) -> Dict[str, Any]:
        """紧急停止（内部方法）"""
        with self._state_lock:
            self._ship_state['target_speed'] = 0.0
            self._ship_state['speed'] = 0.0
            self._ship_state['engine_power'] = 0.0
            self._ship_state['status'] = 'emergency_stop'
            
            self.log_warning("执行紧急停止！")
            
            return {
                'success': True,
                'message': '紧急停止已执行'
            }
    
    # ==================== 控制循环 ====================
    
    def _start_control_loop(self):
        """启动控制循环"""
        if self._control_running:
            return
        
        self._control_running = True
        self._control_thread = threading.Thread(
            target=self._control_loop,
            name=f"{self.plugin_id}-control-loop",
            daemon=True
        )
        self._control_thread.start()
        self.log_info("控制循环已启动")
    
    def _stop_control_loop(self):
        """停止控制循环"""
        if not self._control_running:
            return
        
        self._control_running = False
        if self._control_thread and self._control_thread.is_alive():
            self._control_thread.join(timeout=2.0)
        self.log_info("控制循环已停止")
    
    def _control_loop(self):
        """控制循环主逻辑"""
        self.log_info("进入控制循环")
        
        while self._control_running and self.is_enabled:
            try:
                with self._state_lock:
                    # 更新速度
                    self._update_speed()
                    
                    # 更新方向
                    self._update_direction()
                    
                    # 更新引擎和舵角
                    self._update_actuators()
                    
                    # 更新时间戳
                    self._ship_state['last_update'] = time.time()
                
                # 定期发布状态更新事件
                if int(time.time() * 10) % 10 == 0:  # 每秒发布一次
                    self.publish_event(
                        event_type="ship.control.status_update",
                        data=self.get_ship_status(),
                        priority=EventPriority.LOW
                    )
                
                # 控制周期
                time.sleep(self._control_params['control_interval'])
                
            except Exception as e:
                self.log_error(f"控制循环异常: {e}")
                time.sleep(1.0)
        
        self.log_info("退出控制循环")
    
    def _update_speed(self):
        """更新速度（需要在锁内调用）"""
        current = self._ship_state['speed']
        target = self._ship_state['target_speed']
        dt = self._control_params['control_interval']
        
        if abs(current - target) < 0.01:
            self._ship_state['speed'] = target
            return
        
        # 根据状态选择加速度
        if self._ship_state['status'] == 'emergency_stop':
            max_accel = self._control_params['emergency_decel']
        else:
            max_accel = self._control_params['max_acceleration']
        
        # 计算速度变化
        speed_diff = target - current
        max_change = max_accel * dt
        
        if abs(speed_diff) <= max_change:
            self._ship_state['speed'] = target
        else:
            self._ship_state['speed'] = current + max_change if speed_diff > 0 else current - max_change
    
    def _update_direction(self):
        """更新方向（需要在锁内调用）"""
        current = self._ship_state['direction']
        target = self._ship_state['target_direction']
        dt = self._control_params['control_interval']
        
        # 计算最短转向角度
        diff = (target - current + 180) % 360 - 180
        
        if abs(diff) < 0.1:
            self._ship_state['direction'] = target
            return
        
        # 限制转向速率
        max_change = self._control_params['max_turn_rate'] * dt
        
        if abs(diff) <= max_change:
            self._ship_state['direction'] = target
        else:
            change = max_change if diff > 0 else -max_change
            self._ship_state['direction'] = (current + change) % 360
    
    def _update_actuators(self):
        """更新执行器状态（引擎和舵）"""
        # 根据目标速度设置引擎功率
        target_speed = self._ship_state['target_speed']
        max_speed = self._control_params['max_speed']
        self._ship_state['engine_power'] = (target_speed / max_speed) * 100.0
        
        # 根据方向差设置舵角
        current_dir = self._ship_state['direction']
        target_dir = self._ship_state['target_direction']
        dir_diff = (target_dir - current_dir + 180) % 360 - 180
        
        # 舵角限制在 -45 到 45 度
        self._ship_state['rudder_angle'] = max(-45, min(45, dir_diff))
    
    # ==================== 插件接口方法 ====================
    
    def get_ship_status(self, input_data: Any = None) -> Dict[str, Any]:
        """获取船只状态（插件接口）"""
        with self._state_lock:
            return {
                'speed': self._ship_state['speed'],
                'direction': self._ship_state['direction'],
                'target_speed': self._ship_state['target_speed'],
                'target_direction': self._ship_state['target_direction'],
                'engine_power': self._ship_state['engine_power'],
                'rudder_angle': self._ship_state['rudder_angle'],
                'status': self._ship_state['status'],
                'last_update': self._ship_state['last_update'],
                'timestamp': time.time()
            }
    
    def set_speed(self, input_data: Any) -> Dict[str, Any]:
        """设置速度（插件接口）"""
        if isinstance(input_data, dict):
            speed = input_data.get('speed', 0.0)
        else:
            speed = float(input_data)
        
        return self._set_target_speed(speed)
    
    def set_direction(self, input_data: Any) -> Dict[str, Any]:
        """设置方向（插件接口）"""
        if isinstance(input_data, dict):
            direction = input_data.get('direction', 0.0)
        else:
            direction = float(input_data)
        
        return self._set_target_direction(direction)
    
    def stop(self, input_data: Any = None) -> Dict[str, Any]:
        """停止（插件接口）"""
        return self._stop_ship()
    
    def emergency_stop(self, input_data: Any = None) -> Dict[str, Any]:
        """紧急停止（插件接口）"""
        return self._emergency_stop_internal()
    
    # ==================== 状态保存与恢复 ====================
    
    def _save_custom_state(self) -> Optional[Dict[str, Any]]:
        """保存自定义状态"""
        with self._state_lock:
            return {
                'ship_state': self._ship_state.copy(),
                'control_params': self._control_params.copy(),
                'event_subscribers': self._event_subscribers.copy()
            }
    
    def _restore_custom_state(self, custom_state: Dict[str, Any]) -> None:
        """恢复自定义状态"""
        with self._state_lock:
            if 'ship_state' in custom_state:
                self._ship_state.update(custom_state['ship_state'])
            if 'control_params' in custom_state:
                self._control_params.update(custom_state['control_params'])
            if 'event_subscribers' in custom_state:
                self._event_subscribers = custom_state['event_subscribers']


# 插件类导出
__plugin_class__ = ShipBasicControlPlugin