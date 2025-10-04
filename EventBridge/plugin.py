"""
事件桥接器插件 - plugin.py

功能：
- 提供event_bus与UDP设备之间的双向消息转换
- 订阅device.command.**事件并转发到UDP设备
- 接收UDP设备上报并发布为event_bus事件
- 支持多设备路由
- 自动心跳应答
"""

import socket
import json
import threading
import time
from typing import Any, Dict, Optional, Tuple
from app.usv.plugin_base import Plugin, Response, PluginState
from app.usv.event_bus import EventData, EventPriority


class EventBridgePlugin(Plugin):
    """事件桥接器插件"""
    
    VERSION = '1.0.0'
    MIN_COMPATIBLE_VERSION = '1.0.0'
    
    def __init__(self, plugin_id: str, plugin_manager):
        super().__init__(plugin_id, plugin_manager)
        
        # 配置参数
        self._listen_port = 13000
        self._devices_config: Dict[str, Dict[str, Any]] = {}
        self._socket_timeout = 1.0
        self._recv_buffer_size = 4096
        
        # UDP Socket
        self._sock: Optional[socket.socket] = None
        
        # 运行状态
        self._running = threading.Event()
        self._receiver_thread: Optional[threading.Thread] = None
        
        # 序列号计数器
        self._seq_counter = 0
        self._seq_lock = threading.Lock()
        
        # 设备地址缓存（从上报消息中学习）
        self._device_addresses: Dict[str, Tuple[str, int]] = {}
        self._address_lock = threading.Lock()
        
        # 统计信息
        self._stats = {
            'messages_sent': 0,
            'messages_received': 0,
            'commands_forwarded': 0,
            'errors': 0,
            'start_time': 0.0
        }
        self._stats_lock = threading.Lock()
        
        # 订阅者ID列表
        self._event_subscribers = []
        
        self.log_info("事件桥接器插件初始化完成")
    
    # ==================== 插件生命周期方法 ====================
    
    def _handle_install(self) -> Response:
        """安装插件"""
        self.log_info("正在安装事件桥接器插件...")
        
        try:
            # 加载配置
            self._listen_port = int(self.get_config('listen_port', 13000))
            self._devices_config = self.get_config('devices', {})
            self._socket_timeout = float(self.get_config('socket_timeout', 1.0))
            self._recv_buffer_size = int(self.get_config('recv_buffer_size', 4096))
            
            # 验证配置
            if not self._devices_config:
                return Response(success=False, data="设备配置不能为空")
            
            if not (1024 <= self._listen_port <= 65535):
                return Response(success=False, data="端口号必须在1024-65535范围内")
            
            self.log_info(f"配置已加载: listen_port={self._listen_port}, devices={list(self._devices_config.keys())}")
            self.log_info("事件桥接器插件安装成功")
            return Response(success=True, data="安装成功")
            
        except Exception as e:
            self.log_error(f"安装插件失败: {e}")
            return Response(success=False, data=str(e))
    
    def _handle_enable(self) -> Response:
        """启用插件"""
        self.log_info("正在启用事件桥接器插件...")
        
        try:
            # 创建并绑定UDP Socket
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.bind(('0.0.0.0', self._listen_port))
            self._sock.settimeout(self._socket_timeout)
            
            self.log_info(f"UDP Socket已绑定: 0.0.0.0:{self._listen_port}")
            
            # 启动接收线程
            self._running.set()
            self._receiver_thread = threading.Thread(
                target=self._uplink_receiver,
                name=f"{self.plugin_id}-receiver",
                daemon=True
            )
            self._receiver_thread.start()
            
            self.log_info("UDP接收线程已启动")
            
            # 订阅设备命令事件
            self._subscribe_command_events()
            
            # 记录启动时间
            with self._stats_lock:
                self._stats['start_time'] = time.time()
            
            # 发布桥接器启动事件
            self.publish_event(
                event_type="bridge.started",
                data={
                    'plugin_id': self.plugin_id,
                    'listen_port': self._listen_port,
                    'devices': list(self._devices_config.keys())
                },
                priority=EventPriority.NORMAL
            )
            
            self.log_info("事件桥接器插件启用成功")
            return Response(success=True, data="启用成功")
            
        except Exception as e:
            self.log_error(f"启用插件失败: {e}")
            self._cleanup()
            return Response(success=False, data=str(e))
    
    def _handle_disable(self) -> Response:
        """禁用插件"""
        self.log_info("正在禁用事件桥接器插件...")
        
        try:
            # 停止运行
            self._running.clear()
            
            # 等待接收线程结束
            if self._receiver_thread and self._receiver_thread.is_alive():
                self._receiver_thread.join(timeout=3.0)
            
            # 清理资源
            self._cleanup()
            
            # 发布桥接器停止事件
            self.publish_event(
                event_type="bridge.stopped",
                data={'plugin_id': self.plugin_id},
                priority=EventPriority.NORMAL
            )
            
            self.log_info("事件桥接器插件禁用成功")
            return Response(success=True, data="禁用成功")
            
        except Exception as e:
            self.log_error(f"禁用插件失败: {e}")
            return Response(success=False, data=str(e))
    
    def _handle_config_update(self, old_config: Dict, new_config: Dict) -> Response:
        """处理配置更新"""
        self.log_info("正在更新配置...")
        
        # 设备配置可以热更新
        if 'devices' in new_config:
            self._devices_config = new_config['devices']
            self.log_info(f"设备配置已更新: {list(self._devices_config.keys())}")
        
        # 其他配置需要重启
        need_restart = False
        if 'listen_port' in new_config and new_config['listen_port'] != self._listen_port:
            need_restart = True
        if 'socket_timeout' in new_config and new_config['socket_timeout'] != self._socket_timeout:
            need_restart = True
        
        if need_restart:
            self.log_warning("端口或超时配置变更，需要重启插件以生效")
            return Response(success=True, data="配置已保存，需要重启插件以生效")
        
        self.log_info("配置更新成功")
        return Response(success=True, data="配置更新成功")
    
    def _cleanup(self):
        """清理资源"""
        try:
            if self._sock:
                self._sock.close()
                self._sock = None
        except Exception as e:
            self.log_warning(f"清理socket时出错: {e}")
        
        with self._address_lock:
            self._device_addresses.clear()
    
    # ==================== 事件订阅 ====================
    
    def _subscribe_command_events(self):
        """订阅设备命令事件"""
        result = self.subscribe_event(
            event_type="device.command.**",
            callback=self._handle_device_command_event,
            priority=EventPriority.HIGH
        )
        if result.success:
            self._event_subscribers.append(result.data['subscriber_id'])
            self.log_info("已订阅事件: device.command.**")
    
    # ==================== 事件处理器 ====================
    
    def _handle_device_command_event(self, event: EventData):
        """处理设备命令事件 - 转换为UDP消息"""
        try:
            data = event.data
            
            # 验证数据格式
            if not isinstance(data, dict):
                self.log_debug("忽略非字典格式的命令事件")
                return
            
            target_device = data.get('target_device')
            device_id = data.get('device_id')
            action = data.get('action')
            
            # 必要字段检查
            if not all([target_device, device_id, action]):
                self.log_debug(f"忽略不完整的命令事件: {data}")
                return
            
            self.log_info(f"收到命令事件: {target_device}.{device_id}.{action}",
                         event_id=event.event_id, source=event.source)
            
            # 检查设备配置
            if target_device not in self._devices_config:
                self.log_error(f"未知设备: {target_device}")
                return
            
            # 构建UDP命令消息
            command_msg = self._build_command_message(
                device_id=device_id,
                action=action,
                value=data.get('value')
            )
            
            # 发送UDP命令
            device_cfg = self._devices_config[target_device]
            success = self._send_udp_command(
                host=device_cfg['host'],
                port=device_cfg['port'],
                message=command_msg,
                target_device=target_device,
                device_id=device_id,
                action=action
            )
            
            if success:
                with self._stats_lock:
                    self._stats['commands_forwarded'] += 1
            
        except Exception as e:
            self.log_error(f"处理命令事件失败: {e}", event_id=event.event_id)
            with self._stats_lock:
                self._stats['errors'] += 1
    
    # ==================== UDP消息处理 ====================
    
    def _build_command_message(self, device_id: str, action: str, value: Any) -> Dict:
        """构建UDP命令消息"""
        # 根据动作类型构建命令
        command = {}
        
        if action == "on":
            command = {"state": True}
        elif action == "off":
            command = {"state": False}
        elif action == "set_speed":
            command = {"speed": value if value is not None else 0.0}
        elif action == "on_fwd":
            command = {"state": True, "direction": True}
        elif action == "on_rev":
            command = {"state": True, "direction": False}
        elif action == "start_forward":
            command = {"action": "start_forward", "speed": value if value is not None else 10.0}
        elif action == "start_backward":
            command = {"action": "start_backward", "speed": value if value is not None else 10.0}
        elif action == "stop":
            command = {"action": "stop"}
        else:
            # 通用格式
            command = {"action": action}
            if value is not None:
                command["value"] = value
        
        # 生成序列号
        with self._seq_lock:
            seq = self._seq_counter
            self._seq_counter += 1
        
        # 完整消息
        return {
            "type": "device_command",
            "seq": seq,
            "device_id": device_id,
            "command": command,
            "timestamp": time.time()
        }
    
    def _send_udp_command(self, host: str, port: int, message: Dict,
                         target_device: str, device_id: str, action: str) -> bool:
        """发送UDP命令到设备"""
        try:
            if not self._sock:
                self.log_error("UDP Socket未初始化")
                return False
            
            data = json.dumps(message).encode('utf-8')
            self._sock.sendto(data, (host, port))
            
            with self._stats_lock:
                self._stats['messages_sent'] += 1
            
            self.log_info(
                f"命令已发送: {host}:{port} <- {device_id}.{action} "
                f"(seq={message.get('seq')})"
            )
            
            return True
            
        except Exception as e:
            self.log_error(f"发送UDP命令失败: {e}")
            with self._stats_lock:
                self._stats['errors'] += 1
            return False
    
    def _uplink_receiver(self):
        """上行消息接收循环"""
        self.log_info("UDP接收线程已启动")
        
        while self._running.is_set() and self.is_enabled:
            try:
                if not self._sock:
                    break
                
                data, addr = self._sock.recvfrom(self._recv_buffer_size)
                
                with self._stats_lock:
                    self._stats['messages_received'] += 1
                
                # 在新线程中处理消息，避免阻塞接收循环
                threading.Thread(
                    target=self._handle_uplink_message,
                    args=(data, addr),
                    daemon=True
                ).start()
                
            except socket.timeout:
                continue
            except Exception as e:
                if self._running.is_set():
                    self.log_error(f"接收上行消息错误: {e}")
                    with self._stats_lock:
                        self._stats['errors'] += 1
        
        self.log_info("UDP接收线程已退出")
    
    def _handle_uplink_message(self, data: bytes, addr: Tuple[str, int]):
        """处理上行消息 - 转换为event_bus事件"""
        try:
            # 解析JSON消息
            message = json.loads(data.decode('utf-8'))
            msg_type = message.get('type')
            source = message.get('source', 'unknown')
            msg_data = message.get('data', {})
            
            self.log_debug(f"收到上行消息: {msg_type} from {addr} (source={source})")
            
            # 缓存设备地址
            if source != 'unknown':
                with self._address_lock:
                    self._device_addresses[source] = addr
            
            # 根据消息类型分发处理
            if msg_type == 'command_response':
                self._handle_command_response(message, source, addr)
            elif msg_type == 'device_status':
                self._handle_device_status(message, source, addr)
            elif msg_type == 'sensor_data':
                self._handle_sensor_data(message, source)
            elif msg_type == 'alert':
                self._handle_alert(message, source)
            elif msg_type == 'heartbeat_report':
                self._handle_heartbeat_report(message, source, addr)
            else:
                self.log_debug(f"未处理的上行消息类型: {msg_type} from {source}")
        
        except json.JSONDecodeError as e:
            self.log_warning(f"JSON解析失败: {e}")
            with self._stats_lock:
                self._stats['errors'] += 1
        except Exception as e:
            self.log_error(f"处理上行消息失败: {e}")
            with self._stats_lock:
                self._stats['errors'] += 1
    
    def _handle_command_response(self, message: Dict, source: str, addr: Tuple[str, int]):
        """处理命令响应消息"""
        msg_data = message.get('data', {})
        device_id = message.get('device_id') or msg_data.get('device_id') or 'unknown'
        
        self.publish_event(
            event_type="device.command.result",
            data={
                "device_id": device_id,
                "result": msg_data,
                "source": source,
                "addr": f"{addr[0]}:{addr[1]}"
            },
            priority=EventPriority.HIGH
        )
        
        self.log_info(f"命令响应已转发: {device_id} (source={source})")
    
    def _handle_device_status(self, message: Dict, source: str, addr: Tuple[str, int]):
        """处理设备状态消息"""
        msg_data = message.get('data', {})
        device_id = msg_data.get('device_id') or message.get('device_id') or 'unknown'
        status = msg_data.get('status') if isinstance(msg_data.get('status'), dict) else msg_data
        
        self.publish_event(
            event_type=f"device.status.{source}",
            data={
                "device_id": device_id,
                "status": status,
                "source": source,
                "addr": f"{addr[0]}:{addr[1]}"
            },
            priority=EventPriority.NORMAL
        )
        
        self.log_debug(f"设备状态已转发: {device_id} (source={source})")
    
    def _handle_sensor_data(self, message: Dict, source: str):
        """处理传感器数据消息"""
        msg_data = message.get('data', {})
        sensor_type = msg_data.get('sensor_type', 'unknown')
        
        self.publish_event(
            event_type=f"sensor.{sensor_type}",
            data=msg_data,
            priority=EventPriority.NORMAL
        )
        
        self.log_debug(f"传感器数据已转发: {sensor_type} (source={source})")
    
    def _handle_alert(self, message: Dict, source: str):
        """处理报警消息"""
        msg_data = message.get('data', {})
        level = msg_data.get('level', 'info')
        
        priority = EventPriority.CRITICAL if level == 'critical' else EventPriority.HIGH
        
        self.publish_event(
            event_type=f"alert.{level}",
            data=msg_data,
            priority=priority
        )
        
        self.log_warning(f"报警已转发: level={level} (source={source})")
    
    def _handle_heartbeat_report(self, message: Dict, source: str, addr: Tuple[str, int]):
        """处理心跳上报消息"""
        msg_data = message.get('data', {})
        
        # 发布心跳事件
        self.publish_event(
            event_type="system.heartbeat",
            data={
                "device": source,
                "device_count": msg_data.get('device_count'),
                "timestamp": msg_data.get('timestamp', time.time())
            },
            priority=EventPriority.LOW
        )
        
        # 发送心跳应答
        orig_seq = message.get('seq')
        self._send_heartbeat_ack(addr, source, orig_seq)
        
        self.log_debug(f"心跳已处理: {source}")
    
    def _send_heartbeat_ack(self, addr: Tuple[str, int], source: str, orig_seq: Optional[int] = None):
        """发送心跳应答"""
        try:
            if not self._sock:
                return
            
            ack_message = {
                "type": "heartbeat_ack",
                "source": "server",
                "target": source,
                "timestamp": time.time()
            }
            
            if orig_seq is not None:
                ack_message['seq'] = orig_seq
            
            data = json.dumps(ack_message).encode('utf-8')
            self._sock.sendto(data, addr)
            
            self.log_debug(f"心跳应答已发送: {addr} (seq={orig_seq})")
            
        except Exception as e:
            self.log_error(f"发送心跳应答失败: {e}")
    
    # ==================== 插件接口方法 ====================
    
    def get_bridge_status(self, input_data: Any = None) -> Dict[str, Any]:
        """获取桥接器状态（插件接口）"""
        with self._stats_lock:
            stats = self._stats.copy()
        
        with self._address_lock:
            learned_devices = list(self._device_addresses.keys())
        
        uptime = time.time() - stats['start_time'] if stats['start_time'] > 0 else 0.0
        
        return {
            'running': self._running.is_set(),
            'listen_port': self._listen_port,
            'configured_devices': list(self._devices_config.keys()),
            'learned_devices': learned_devices,
            'statistics': {
                'uptime': uptime,
                'messages_sent': stats['messages_sent'],
                'messages_received': stats['messages_received'],
                'commands_forwarded': stats['commands_forwarded'],
                'errors': stats['errors']
            }
        }
    
    def get_device_address(self, input_data: Any) -> Dict[str, Any]:
        """获取设备实际地址（插件接口）"""
        if not isinstance(input_data, dict):
            return {'success': False, 'message': '输入必须是字典格式'}
        
        device_name = input_data.get('device_name')
        if not device_name:
            return {'success': False, 'message': '缺少device_name参数'}
        
        with self._address_lock:
            addr = self._device_addresses.get(device_name)
        
        if addr:
            return {
                'success': True,
                'host': addr[0],
                'port': addr[1]
            }
        else:
            return {
                'success': False,
                'message': f'未知设备或设备未上报: {device_name}'
            }
    
    def send_raw_udp(self, input_data: Any) -> Dict[str, Any]:
        """发送原始UDP消息（插件接口，调试用）"""
        if not isinstance(input_data, dict):
            return {'success': False, 'message': '输入必须是字典格式'}
        
        host = input_data.get('host')
        port = input_data.get('port')
        message = input_data.get('message')
        
        if not all([host, port, message]):
            return {'success': False, 'message': '缺少必要参数: host, port, message'}
        
        try:
            if not self._sock:
                return {'success': False, 'message': 'UDP Socket未初始化'}
            
            data = json.dumps(message).encode('utf-8')
            self._sock.sendto(data, (host, int(port)))
            
            return {'success': True, 'message': f'消息已发送到 {host}:{port}'}
            
        except Exception as e:
            return {'success': False, 'message': str(e)}
    
    # ==================== 状态保存与恢复 ====================
    
    def _save_custom_state(self) -> Optional[Dict[str, Any]]:
        """保存自定义状态"""
        with self._stats_lock:
            stats = self._stats.copy()
        
        with self._address_lock:
            addresses = self._device_addresses.copy()
        
        return {
            'statistics': stats,
            'device_addresses': addresses,
            'event_subscribers': self._event_subscribers.copy()
        }
    
    def _restore_custom_state(self, custom_state: Dict[str, Any]) -> None:
        """恢复自定义状态"""
        if 'statistics' in custom_state:
            with self._stats_lock:
                self._stats.update(custom_state['statistics'])
        
        if 'device_addresses' in custom_state:
            with self._address_lock:
                self._device_addresses = custom_state['device_addresses']
        
        if 'event_subscribers' in custom_state:
            self._event_subscribers = custom_state['event_subscribers']


# 插件类导出
__plugin_class__ = EventBridgePlugin