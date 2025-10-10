"""
事件桥接器插件 - WebSocket版本 (纯指令流)

核心功能：
1. ✅ WebSocket长连接管理
2. ✅ 设备指令下发（Event → WebSocket）
3. ✅ 设备响应上报（WebSocket → Event）
4. ✅ 心跳保活机制
5. ✅ 连接状态监控
"""

import asyncio
import websockets
import json
import threading
import time
import traceback
from typing import Any, Dict, Optional, Set
from collections import defaultdict
from app.usv.plugin_base import Plugin, Response, PluginState
from app.usv.event_bus import EventData, EventPriority


class EventBridgePlugin(Plugin):
    """事件桥接器插件 - WebSocket指令流专用"""

    VERSION = '2.0.0'
    MIN_COMPATIBLE_VERSION = '2.0.0'

    def __init__(self, plugin_id: str, plugin_manager):
        super().__init__(plugin_id, plugin_manager)

        # 配置参数
        self._listen_port = 13000
        self._devices_config: Dict[str, Dict[str, Any]] = {}

        # WebSocket服务器和连接管理
        self._ws_server = None
        self._all_clients: Set[websockets.WebSocketServerProtocol] = set()
        self._device_connections: Dict[str, websockets.WebSocketServerProtocol] = {}
        self._connection_lock = threading.Lock()

        # 异步事件循环
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server_thread: Optional[threading.Thread] = None

        # 运行状态
        self._running = threading.Event()
        self._server_ready = threading.Event()

        # 序列号计数器
        self._seq_counter = 0
        self._seq_lock = threading.Lock()

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

        self.log_info("事件桥接器插件初始化完成 (WebSocket指令流 v2.0.0)")

    # ==================== 插件生命周期方法 ====================

    def _handle_install(self) -> Response:
        """安装插件"""
        self.log_info("正在安装事件桥接器插件...")

        try:
            self._listen_port = int(self.get_config('listen_port', 13000))
            self._devices_config = self.get_config('devices', {})

            if not self._devices_config:
                return Response(success=False, data="设备配置不能为空")

            if not (1024 <= self._listen_port <= 65535):
                return Response(success=False, data="端口号必须在1024-65535范围内")

            self.log_info(f"配置已加载: listen_port={self._listen_port}, devices={list(self._devices_config.keys())}")
            self.log_info("事件桥接器插件安装成功")
            return Response(success=True, data="安装成功")

        except Exception as e:
            self.log_error(f"安装插件失败: {e}\n{traceback.format_exc()}")
            return Response(success=False, data=str(e))

    def _handle_enable(self) -> Response:
        """启用插件"""
        self.log_info("正在启用事件桥接器插件...")

        try:
            self._server_ready.clear()
            
            self._running.set()
            self._server_thread = threading.Thread(
                target=self._run_websocket_server,
                name=f"{self.plugin_id}-ws-server",
                daemon=True
            )
            self._server_thread.start()

            if not self._server_ready.wait(timeout=10.0):
                raise Exception("WebSocket服务器启动超时")

            self.log_info(f"✓ WebSocket服务器已启动: ws://0.0.0.0:{self._listen_port}")
            self.log_info("  - 专用于设备指令流通信")

            self._subscribe_command_events()

            with self._stats_lock:
                self._stats['start_time'] = time.time()

            self.publish_event(
                event_type="bridge.started",
                data={
                    'plugin_id': self.plugin_id,
                    'listen_port': self._listen_port,
                    'devices': list(self._devices_config.keys()),
                    'protocol': 'websocket',
                    'purpose': 'command_stream'
                },
                priority=EventPriority.NORMAL
            )

            self.log_info("事件桥接器插件启用成功 (指令流)")
            return Response(success=True, data="启用成功")

        except Exception as e:
            self.log_error(f"启用插件失败: {e}\n{traceback.format_exc()}")
            self._cleanup()
            return Response(success=False, data=str(e))

    def _handle_disable(self) -> Response:
        """禁用插件"""
        self.log_info("正在禁用事件桥接器插件...")

        try:
            self._running.clear()

            if self._loop and self._ws_server:
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        self._shutdown_server(),
                        self._loop
                    )
                    future.result(timeout=5.0)
                except Exception as e:
                    self.log_warning(f"关闭服务器时出错: {e}")

            if self._server_thread and self._server_thread.is_alive():
                self._server_thread.join(timeout=5.0)
                if self._server_thread.is_alive():
                    self.log_warning("服务器线程未能在超时时间内结束")

            self._cleanup()

            self.publish_event(
                event_type="bridge.stopped",
                data={'plugin_id': self.plugin_id},
                priority=EventPriority.NORMAL
            )

            self.log_info("事件桥接器插件禁用成功")
            return Response(success=True, data="禁用成功")

        except Exception as e:
            self.log_error(f"禁用插件失败: {e}\n{traceback.format_exc()}")
            return Response(success=False, data=str(e))

    def _handle_config_update(self, old_config: Dict, new_config: Dict) -> Response:
        """处理配置更新"""
        self.log_info("正在更新配置...")

        if 'devices' in new_config:
            self._devices_config = new_config['devices']
            self.log_info(f"设备配置已更新: {list(self._devices_config.keys())}")

        need_restart = False
        if 'listen_port' in new_config and new_config['listen_port'] != self._listen_port:
            need_restart = True

        if need_restart:
            self.log_warning("端口配置变更，需要重启插件以生效")
            return Response(success=True, data="配置已保存，需要重启插件以生效")

        self.log_info("配置更新成功")
        return Response(success=True, data="配置更新成功")

    def _cleanup(self):
        """清理资源"""
        try:
            with self._connection_lock:
                self._all_clients.clear()
                self._device_connections.clear()
                
        except Exception as e:
            self.log_warning(f"清理连接时出错: {e}")

    # ==================== WebSocket服务器 ====================

    def _run_websocket_server(self):
        """运行WebSocket服务器"""
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            
            self._loop.run_until_complete(self._start_server())
            
        except Exception as e:
            self.log_error(f"WebSocket服务器线程错误: {e}\n{traceback.format_exc()}")
            self._server_ready.set()
        finally:
            if self._loop:
                try:
                    self._loop.close()
                except Exception as e:
                    self.log_warning(f"关闭事件循环时出错: {e}")

    async def _start_server(self):
        """启动WebSocket服务器"""
        try:
            self.log_info(f"正在绑定 WebSocket 服务器到 0.0.0.0:{self._listen_port}...")
            
            async with websockets.serve(
                self._handle_client,
                '0.0.0.0', 
                self._listen_port,
                ping_interval=30,
                ping_timeout=10,
                max_size=1 * 1024 * 1024,  # 1MB最大消息（指令流不需要太大）
            ) as server:
                self._ws_server = server
                
                self.log_info(f"✓ WebSocket 服务器成功绑定到端口 {self._listen_port}")
                self.log_info("  - 专用于设备指令流通信")
                
                self._server_ready.set()
                
                stop_future = asyncio.Future()
                
                async def check_running():
                    while self._running.is_set():
                        await asyncio.sleep(1)
                    stop_future.set_result(None)
                
                asyncio.create_task(check_running())
                
                await stop_future
                
                self.log_info("WebSocket 服务器正在关闭...")

        except OSError as e:
            self.log_error(f"WebSocket 服务器启动失败 (端口可能被占用): {e}\n{traceback.format_exc()}")
            self._server_ready.set()
            
        except Exception as e:
            self.log_error(f"WebSocket 服务器启动失败: {e}\n{traceback.format_exc()}")
            self._server_ready.set()

    async def _shutdown_server(self):
        """优雅关闭服务器"""
        if self._ws_server:
            self._ws_server.close()
            await self._ws_server.wait_closed()

    # ==================== 连接处理器 ====================

    async def _handle_client(self, websocket):
        """处理客户端连接"""
        client_addr = websocket.remote_address
        self.log_info(f"新客户端连接: {client_addr}")

        with self._connection_lock:
            self._all_clients.add(websocket)

        device_name = None

        try:
            async for message in websocket:
                if isinstance(message, str):
                    device_name = await self._handle_text_message(message, websocket, device_name)
                else:
                    self.log_warning(f"收到非文本消息: {type(message)}")

        except websockets.exceptions.ConnectionClosed:
            self.log_info(f"客户端断开: {client_addr}")
        except Exception as e:
            self.log_error(f"处理客户端消息错误: {e}\n{traceback.format_exc()}")
        finally:
            await self._cleanup_client_connection(websocket, device_name)

    async def _cleanup_client_connection(self, websocket, device_name: Optional[str]):
        """清理客户端连接"""
        with self._connection_lock:
            self._all_clients.discard(websocket)
            
            if device_name and device_name in self._device_connections:
                if self._device_connections[device_name] == websocket:
                    del self._device_connections[device_name]
                    self.log_warning(f"设备 {device_name} 已断开连接")

    # ==================== 消息处理 ====================

    async def _handle_text_message(self, data: str, websocket, device_name: Optional[str]) -> Optional[str]:
        """处理文本消息"""
        try:
            with self._stats_lock:
                self._stats['messages_received'] += 1

            message = json.loads(data)
            msg_type = message.get('type')
            source = message.get('source', 'unknown')

            self.log_debug(f"收到消息: {msg_type} from {websocket.remote_address} (source={source})")

            # 设备注册
            if source != 'unknown' and source != device_name:
                with self._connection_lock:
                    self._device_connections[source] = websocket
                self.log_info(f"设备 {source} 已注册连接")
                device_name = source

            # 处理不同类型的消息
            if msg_type == 'heartbeat_report':
                await self._handle_heartbeat_report(message, source, websocket)
            elif msg_type == 'command_response':
                self._handle_command_response(message, source)
            elif msg_type == 'device_status':
                self._handle_device_status(message, source)
            elif msg_type == 'sensor_data':
                self._handle_sensor_data(message, source)
            elif msg_type == 'alert':
                self._handle_alert(message, source)
            else:
                self.log_debug(f"未处理的消息类型: {msg_type} from {source}")

            return device_name

        except json.JSONDecodeError as e:
            self.log_warning(f"JSON解析失败: {e}")
            with self._stats_lock:
                self._stats['errors'] += 1
            return device_name
        except Exception as e:
            self.log_error(f"处理消息失败: {e}\n{traceback.format_exc()}")
            with self._stats_lock:
                self._stats['errors'] += 1
            return device_name

    def _handle_command_response(self, message: Dict, source: str):
        """处理命令响应消息"""
        msg_data = message.get('data', {})
        device_id = message.get('device_id') or msg_data.get('device_id') or 'unknown'
        
        self.publish_event(
            event_type="device.command.result",
            data={
                "device_id": device_id,
                "result": msg_data,
                "source": source
            },
            priority=EventPriority.HIGH
        )
        
        self.log_info(f"命令响应已转发: {device_id} (source={source})")

    def _handle_device_status(self, message: Dict, source: str):
        """处理设备状态消息"""
        msg_data = message.get('data', {})
        device_id = msg_data.get('device_id') or message.get('device_id') or 'unknown'
        status = msg_data.get('status') if isinstance(msg_data.get('status'), dict) else msg_data
        
        self.publish_event(
            event_type=f"device.status.{source}",
            data={
                "device_id": device_id,
                "status": status,
                "source": source
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

    async def _handle_heartbeat_report(self, message: Dict, source: str, websocket):
        """处理心跳上报消息"""
        msg_data = message.get('data', {})
        
        self.publish_event(
            event_type="system.heartbeat",
            data={
                "device": source,
                "device_count": msg_data.get('device_count'),
                "timestamp": msg_data.get('timestamp', time.time())
            },
            priority=EventPriority.LOW
        )
        
        await self._send_heartbeat_ack(websocket, source, message.get('seq'))
        
        self.log_debug(f"心跳已处理: {source}")

    async def _send_heartbeat_ack(self, websocket, source: str, orig_seq: Optional[int] = None):
        """发送心跳应答"""
        try:
            ack_message = {
                "type": "heartbeat_ack",
                "source": "server",
                "target": source,
                "timestamp": time.time()
            }
            
            if orig_seq is not None:
                ack_message['seq'] = orig_seq
            
            data = json.dumps(ack_message)
            await websocket.send(data)
            
            self.log_debug(f"心跳应答已发送: {source} (seq={orig_seq})")
            
        except Exception as e:
            self.log_error(f"发送心跳应答失败: {e}")

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
        """处理设备命令事件 - 转换为WebSocket消息"""
        try:
            data = event.data
            
            if not isinstance(data, dict):
                self.log_debug("忽略非字典格式的命令事件")
                return
            
            target_device = data.get('target_device')
            device_id = data.get('device_id')
            action = data.get('action')
            
            if not all([target_device, device_id, action]):
                self.log_debug(f"忽略不完整的命令事件: {data}")
                return
            
            self.log_info(f"收到命令事件: {target_device}.{device_id}.{action}",
                         event_id=event.event_id, source=event.source)
            
            if target_device not in self._devices_config:
                self.log_error(f"未知设备: {target_device}")
                return
            
            with self._connection_lock:
                websocket = self._device_connections.get(target_device)
            
            if not websocket:
                self.log_error(f"设备未连接: {target_device}")
                return
            
            command_msg = self._build_command_message(
                device_id=device_id,
                action=action,
                value=data.get('value')
            )
            
            success = self._send_websocket_command(
                websocket=websocket,
                message=command_msg,
                target_device=target_device,
                device_id=device_id,
                action=action
            )
            
            if success:
                with self._stats_lock:
                    self._stats['commands_forwarded'] += 1
            
        except Exception as e:
            self.log_error(f"处理命令事件失败: {e}\n{traceback.format_exc()}", event_id=event.event_id)
            with self._stats_lock:
                self._stats['errors'] += 1

    # ==================== 消息构建和发送 ====================

    def _build_command_message(self, device_id: str, action: str, value: Any) -> Dict:
        """构建命令消息"""
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
            command = {"action": action}
            if value is not None:
                command["value"] = value
        
        with self._seq_lock:
            seq = self._seq_counter
            self._seq_counter += 1
        
        return {
            "type": "device_command",
            "seq": seq,
            "device_id": device_id,
            "command": command,
            "timestamp": time.time()
        }

    def _send_websocket_command(self, websocket, message: Dict,
                                target_device: str, device_id: str, action: str) -> bool:
        """发送WebSocket命令到设备"""
        try:
            if not self._loop:
                self.log_error("事件循环未初始化")
                return False
            
            data = json.dumps(message)
            
            future = asyncio.run_coroutine_threadsafe(
                websocket.send(data),
                self._loop
            )
            future.result(timeout=5.0)
            
            with self._stats_lock:
                self._stats['messages_sent'] += 1
            
            self.log_info(
                f"命令已发送: {target_device} <- {device_id}.{action} "
                f"(seq={message.get('seq')})"
            )
            
            return True
            
        except Exception as e:
            self.log_error(f"发送WebSocket命令失败: {e}\n{traceback.format_exc()}")
            with self._stats_lock:
                self._stats['errors'] += 1
            return False

    # ==================== 插件接口方法 ====================

    def get_bridge_status(self, input_data: Any = None) -> Dict[str, Any]:
        """获取桥接器状态"""
        with self._stats_lock:
            stats = self._stats.copy()
        
        with self._connection_lock:
            connected_devices = list(self._device_connections.keys())
            total_client_count = len(self._all_clients)
        
        uptime = time.time() - stats['start_time'] if stats['start_time'] > 0 else 0.0
        
        return {
            'running': self._running.is_set(),
            'listen_port': self._listen_port,
            'protocol': 'websocket',
            'purpose': 'command_stream',
            'configured_devices': list(self._devices_config.keys()),
            'connected_devices': connected_devices,
            'client_count': total_client_count,
            'statistics': {
                'uptime': uptime,
                'messages_sent': stats['messages_sent'],
                'messages_received': stats['messages_received'],
                'commands_forwarded': stats['commands_forwarded'],
                'errors': stats['errors']
            }
        }

    def get_device_connection(self, input_data: Any) -> Dict[str, Any]:
        """获取设备连接状态"""
        if not isinstance(input_data, dict):
            return {'success': False, 'message': '输入必须是字典格式'}
        
        device_name = input_data.get('device_name')
        if not device_name:
            return {'success': False, 'message': '缺少device_name参数'}
        
        with self._connection_lock:
            websocket = self._device_connections.get(device_name)
        
        if websocket:
            return {
                'success': True,
                'connected': True,
                'remote_address': str(websocket.remote_address)
            }
        else:
            return {
                'success': True,
                'connected': False,
                'remote_address': None
            }

    def broadcast_message(self, input_data: Any) -> Dict[str, Any]:
        """广播消息到所有客户端"""
        if not isinstance(input_data, dict):
            return {'success': False, 'message': '输入必须是字典格式'}
        
        message = input_data.get('message')
        if not message:
            return {'success': False, 'message': '缺少message参数'}
        
        try:
            if not self._loop:
                return {'success': False, 'message': '事件循环未初始化'}
            
            data = json.dumps(message)
            
            with self._connection_lock:
                clients = self._all_clients.copy()
            
            success_count = 0
            for client in clients:
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        client.send(data),
                        self._loop
                    )
                    future.result(timeout=2.0)
                    success_count += 1
                except Exception as e:
                    self.log_warning(f"广播到 {client.remote_address} 失败: {e}")
            
            return {
                'success': True,
                'message': f'消息已广播到 {success_count} 个客户端'
            }
            
        except Exception as e:
            return {'success': False, 'message': str(e)}

    # ==================== 状态保存与恢复 ====================

    def _save_custom_state(self) -> Optional[Dict[str, Any]]:
        """保存自定义状态"""
        with self._stats_lock:
            stats = self._stats.copy()
        
        with self._connection_lock:
            devices = list(self._device_connections.keys())
        
        return {
            'statistics': stats,
            'connected_devices': devices,
            'event_subscribers': self._event_subscribers.copy()
        }

    def _restore_custom_state(self, custom_state: Dict[str, Any]) -> None:
        """恢复自定义状态"""
        if 'statistics' in custom_state:
            with self._stats_lock:
                self._stats.update(custom_state['statistics'])
        
        if 'event_subscribers' in custom_state:
            self._event_subscribers = custom_state['event_subscribers']


__plugin_class__ = EventBridgePlugin