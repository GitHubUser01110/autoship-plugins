"""
事件桥接器插件 - plugin.py (WebSocket版本 + 摄像头支持 + 双连接架构)

架构改进：
1. 双连接架构：/ingest_video（视频流）+ /ingest_boxes（检测框）
2. 路径路由处理：根据WebSocket连接路径分发处理
3. 连接类型分类：视频流连接、检测框连接、通用连接
4. 专门的处理方法：针对不同数据类型优化处理逻辑
5. 增强的统计和监控：分类统计不同类型连接的数据传输
"""

import asyncio
import websockets
import json
import threading
import time
import struct
import traceback
from typing import Any, Dict, Optional, Set, Union
from collections import defaultdict
from app.usv.plugin_base import Plugin, Response, PluginState
from app.usv.event_bus import EventData, EventPriority


class EventBridgePlugin(Plugin):
    """事件桥接器插件 (WebSocket版本 + 摄像头支持 + 双连接架构)"""

    VERSION = '1.1.0'
    MIN_COMPATIBLE_VERSION = '1.0.0'

    def __init__(self, plugin_id: str, plugin_manager):
        super().__init__(plugin_id, plugin_manager)

        # 配置参数
        self._listen_port = 13000
        self._devices_config: Dict[str, Dict[str, Any]] = {}

        # WebSocket服务器和连接管理
        self._ws_server = None
        
        # 连接分类管理
        self._video_clients: Set[websockets.WebSocketServerProtocol] = set()
        self._detection_clients: Set[websockets.WebSocketServerProtocol] = set()
        self._generic_clients: Set[websockets.WebSocketServerProtocol] = set()
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

        # 统计信息（增强版）
        self._stats = {
            'messages_sent': 0,
            'messages_received': 0,
            'commands_forwarded': 0,
            'frames_forwarded': 0,
            'frames_received': 0,  # 新增：接收的帧数
            'detections_received': 0,  # 新增：接收的检测框消息数
            'errors': 0,
            'start_time': 0.0
        }
        self._stats_lock = threading.Lock()

        # 订阅者ID列表
        self._event_subscribers = []

        # 摄像头流管理
        self._camera_streams: Dict[str, Dict[str, Any]] = {}
        self._camera_lock = threading.Lock()
        
        # 帧缓存和订阅管理
        self._camera_frame_cache: Dict[str, bytes] = {}  # {camera_id: latest_frame_data}
        self._frame_subscribers: Dict[str, Set[websockets.WebSocketServerProtocol]] = defaultdict(set)
        self._subscriber_cameras: Dict[websockets.WebSocketServerProtocol, Set[str]] = defaultdict(set)
        self._frame_cache_lock = threading.Lock()

        self.log_info("事件桥接器插件初始化完成 (双连接架构)")

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
            self.log_info("  - 视频流端点: /ingest_video")
            self.log_info("  - 检测框端点: /ingest_boxes")
            self.log_info("  - 通用端点: / (向后兼容)")

            self._subscribe_command_events()

            with self._stats_lock:
                self._stats['start_time'] = time.time()

            self.publish_event(
                event_type="bridge.started",
                data={
                    'plugin_id': self.plugin_id,
                    'listen_port': self._listen_port,
                    'devices': list(self._devices_config.keys()),
                    'endpoints': ['/ingest_video', '/ingest_boxes', '/']
                },
                priority=EventPriority.NORMAL
            )

            self.log_info("事件桥接器插件启用成功")
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
                self._video_clients.clear()
                self._detection_clients.clear()
                self._generic_clients.clear()
                self._all_clients.clear()
                self._device_connections.clear()
            
            with self._camera_lock:
                self._camera_streams.clear()
            
            with self._frame_cache_lock:
                self._camera_frame_cache.clear()
                self._frame_subscribers.clear()
                self._subscriber_cameras.clear()
                
        except Exception as e:
            self.log_warning(f"清理连接时出错: {e}")

    # ==================== WebSocket服务器（双连接架构）====================

    def _run_websocket_server(self):
        """运行WebSocket服务器（在独立线程中）"""
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
        """启动WebSocket服务器（带路由功能）- 修复版"""
        try:
            self.log_info(f"正在绑定 WebSocket 服务器到 0.0.0.0:{self._listen_port}...")
            
            # 修复：确保 router 函数正确定义
            async def router(websocket):
                # 从 websocket 对象获取路径
                path = websocket.path
                self.log_debug(f"新连接: {websocket.remote_address} -> {path}")
                
                if path == '/ingest_video':
                    await self._handle_video_stream(websocket)
                elif path == '/ingest_boxes':
                    await self._handle_detection_boxes(websocket)
                else:
                    # 通用处理（向后兼容）
                    await self._handle_generic_client(websocket)
            
            # 使用修复后的 router
            async with websockets.serve(
                router,
                '0.0.0.0', 
                self._listen_port,
                ping_interval=30,
                ping_timeout=10,
                max_size=10 * 1024 * 1024
            ) as server:
                self._ws_server = server
                
                self.log_info(f"✓ WebSocket 服务器成功绑定到端口 {self._listen_port}")
                self.log_info("  - 视频流端点: /ingest_video")
                self.log_info("  - 检测框端点: /ingest_boxes")
                self.log_info("  - 通用端点: / (向后兼容)")
                
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

    # ==================== 专门的连接处理器 ====================

    async def _handle_video_stream(self, websocket):
        """专门处理视频流连接 (/ingest_video)"""
        client_addr = websocket.remote_address
        self.log_info(f"视频流客户端连接: {client_addr}")

        with self._connection_lock:
            self._video_clients.add(websocket)
            self._all_clients.add(websocket)

        device_name = None

        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    # 处理二进制视频帧
                    device_name = await self._handle_camera_frame(message, websocket, device_name)
                elif isinstance(message, str):
                    # 处理JSON消息（如心跳、设备注册等）
                    device_name = await self._handle_text_message(message, websocket, device_name)

        except websockets.exceptions.ConnectionClosed:
            self.log_info(f"视频流客户端断开: {client_addr}")
        except Exception as e:
            self.log_error(f"处理视频流客户端消息错误: {e}\n{traceback.format_exc()}")
        finally:
            await self._cleanup_client_connection(websocket, device_name, 'video')

    async def _handle_detection_boxes(self, websocket):
        """专门处理检测框连接 (/ingest_boxes)"""
        client_addr = websocket.remote_address
        self.log_info(f"检测框客户端连接: {client_addr}")

        with self._connection_lock:
            self._detection_clients.add(websocket)
            self._all_clients.add(websocket)

        device_name = None

        try:
            async for message in websocket:
                if isinstance(message, str):
                    # 处理检测框JSON消息
                    device_name = await self._handle_detection_message(message, websocket, device_name)
                else:
                    self.log_warning(f"检测框连接收到非文本消息: {type(message)}")

        except websockets.exceptions.ConnectionClosed:
            self.log_info(f"检测框客户端断开: {client_addr}")
        except Exception as e:
            self.log_error(f"处理检测框客户端消息错误: {e}\n{traceback.format_exc()}")
        finally:
            await self._cleanup_client_connection(websocket, device_name, 'detection')

    async def _handle_generic_client(self, websocket):
        """处理通用客户端连接（向后兼容）"""
        client_addr = websocket.remote_address
        self.log_info(f"通用客户端连接: {client_addr}")

        with self._connection_lock:
            self._generic_clients.add(websocket)
            self._all_clients.add(websocket)

        device_name = None

        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    # 处理二进制数据（摄像头帧）
                    device_name = await self._handle_camera_frame(message, websocket, device_name)
                elif isinstance(message, str):
                    # 处理文本消息
                    device_name = await self._handle_uplink_message(message, websocket, device_name)

        except websockets.exceptions.ConnectionClosed:
            self.log_info(f"通用客户端断开: {client_addr}")
        except Exception as e:
            self.log_error(f"处理通用客户端消息错误: {e}\n{traceback.format_exc()}")
        finally:
            await self._cleanup_client_connection(websocket, device_name, 'generic')

    async def _cleanup_client_connection(self, websocket, device_name: Optional[str], connection_type: str):
        """清理客户端连接"""
        with self._connection_lock:
            self._all_clients.discard(websocket)
            
            if connection_type == 'video':
                self._video_clients.discard(websocket)
            elif connection_type == 'detection':
                self._detection_clients.discard(websocket)
            elif connection_type == 'generic':
                self._generic_clients.discard(websocket)
            
            if device_name and device_name in self._device_connections:
                if self._device_connections[device_name] == websocket:
                    del self._device_connections[device_name]
                    self.log_warning(f"设备 {device_name} 已断开连接 (类型: {connection_type})")
        
        # 清理订阅
        await self._cleanup_subscriber(websocket)

    # ==================== 消息处理方法 ====================

    async def _handle_detection_message(self, data: str, websocket, device_name: Optional[str]) -> Optional[str]:
        """处理检测框JSON消息"""
        try:
            with self._stats_lock:
                self._stats['messages_received'] += 1
                self._stats['detections_received'] += 1

            message = json.loads(data)
            
            # 检测设备注册
            if not device_name:
                inferred_device = message.get('device_name') or message.get('source')
                if inferred_device:
                    device_name = inferred_device
                    with self._connection_lock:
                        self._device_connections[device_name] = websocket
                    self.log_info(f"设备 {device_name} 已通过检测框连接注册")

            # 发布检测事件
            self.publish_event(
                event_type="detection.boxes",
                data={
                    "camera_id": message.get('camera_id'),
                    "device_name": message.get('device_name') or device_name,
                    "detections": message.get('det', []),
                    "frame_id": message.get('id'),
                    "timestamp": message.get('ts'),
                    "scale": message.get('scale'),
                    "frame_size": (message.get('w'), message.get('h')),
                    "detection_count": len(message.get('det', []))
                },
                priority=EventPriority.NORMAL
            )

            det_count = len(message.get('det', []))
            self.log_debug(f"检测框已处理: {det_count}个目标 (camera_id: {message.get('camera_id')})")

            return device_name

        except json.JSONDecodeError as e:
            self.log_warning(f"检测框JSON解析失败: {e}")
            with self._stats_lock:
                self._stats['errors'] += 1
            return device_name
        except Exception as e:
            self.log_error(f"处理检测框消息失败: {e}\n{traceback.format_exc()}")
            with self._stats_lock:
                self._stats['errors'] += 1
            return device_name

    async def _handle_text_message(self, data: str, websocket, device_name: Optional[str]) -> Optional[str]:
        """处理通用文本消息"""
        try:
            with self._stats_lock:
                self._stats['messages_received'] += 1

            message = json.loads(data)
            msg_type = message.get('type')
            source = message.get('source', 'unknown')

            self.log_debug(f"收到文本消息: {msg_type} from {websocket.remote_address} (source={source})")

            # 设备注册处理
            if source != 'unknown' and source != device_name:
                with self._connection_lock:
                    self._device_connections[source] = websocket
                self.log_info(f"设备 {source} 已注册连接")
                device_name = source

            # 消息类型处理
            if msg_type == 'heartbeat_report':
                await self._handle_heartbeat_report(message, source, websocket)
            elif msg_type == 'subscribe_camera':
                camera_id = message.get('camera_id')
                if camera_id:
                    await self._subscribe_camera_stream(websocket, camera_id)
            elif msg_type == 'unsubscribe_camera':
                camera_id = message.get('camera_id')
                if camera_id:
                    await self._unsubscribe_camera_stream(websocket, camera_id)
            else:
                # 其他消息类型转发到通用处理器
                device_name = await self._handle_uplink_message(data, websocket, device_name)

            return device_name

        except json.JSONDecodeError as e:
            self.log_warning(f"文本消息JSON解析失败: {e}")
            with self._stats_lock:
                self._stats['errors'] += 1
            return device_name
        except Exception as e:
            self.log_error(f"处理文本消息失败: {e}\n{traceback.format_exc()}")
            with self._stats_lock:
                self._stats['errors'] += 1
            return device_name

    # ==================== 订阅管理 ====================
    
    async def _subscribe_camera_stream(self, websocket, camera_id: str):
        """订阅摄像头流"""
        with self._frame_cache_lock:
            self._frame_subscribers[camera_id].add(websocket)
            self._subscriber_cameras[websocket].add(camera_id)
        
        self.log_info(f"客户端订阅摄像头流: {camera_id} ({websocket.remote_address})")
        
        # 发送订阅确认
        ack_msg = {
            "type": "subscribe_ack",
            "camera_id": camera_id,
            "status": "success",
            "timestamp": time.time()
        }
        
        try:
            await websocket.send(json.dumps(ack_msg))
            
            # 如果有缓存的最新帧，立即发送
            if camera_id in self._camera_frame_cache:
                await websocket.send(self._camera_frame_cache[camera_id])
                self.log_debug(f"发送缓存帧给新订阅者: {camera_id}")
                
        except Exception as e:
            self.log_error(f"发送订阅确认失败: {e}")
    
    async def _unsubscribe_camera_stream(self, websocket, camera_id: str):
        """取消订阅摄像头流"""
        with self._frame_cache_lock:
            if camera_id in self._frame_subscribers:
                self._frame_subscribers[camera_id].discard(websocket)
                if not self._frame_subscribers[camera_id]:
                    del self._frame_subscribers[camera_id]
            
            if websocket in self._subscriber_cameras:
                self._subscriber_cameras[websocket].discard(camera_id)
                if not self._subscriber_cameras[websocket]:
                    del self._subscriber_cameras[websocket]
        
        self.log_info(f"客户端取消订阅摄像头流: {camera_id} ({websocket.remote_address})")
        
        # 发送取消订阅确认
        ack_msg = {
            "type": "unsubscribe_ack",
            "camera_id": camera_id,
            "status": "success",
            "timestamp": time.time()
        }
        
        try:
            await websocket.send(json.dumps(ack_msg))
        except Exception as e:
            self.log_error(f"发送取消订阅确认失败: {e}")
    
    async def _cleanup_subscriber(self, websocket):
        """清理订阅者"""
        with self._frame_cache_lock:
            # 获取该客户端订阅的所有摄像头
            subscribed_cameras = self._subscriber_cameras.get(websocket, set()).copy()
            
            # 从所有订阅中移除该客户端
            for camera_id in subscribed_cameras:
                if camera_id in self._frame_subscribers:
                    self._frame_subscribers[camera_id].discard(websocket)
                    if not self._frame_subscribers[camera_id]:
                        del self._frame_subscribers[camera_id]
            
            # 移除客户端记录
            if websocket in self._subscriber_cameras:
                del self._subscriber_cameras[websocket]
        
        if subscribed_cameras:
            self.log_info(f"清理订阅者: {websocket.remote_address}, 订阅的摄像头: {subscribed_cameras}")

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
    
    async def _handle_uplink_message(self, data: str, websocket, 
                                    current_device_name: Optional[str]) -> Optional[str]:
        """处理上行消息（兼容旧版本）"""
        try:
            with self._stats_lock:
                self._stats['messages_received'] += 1
            
            message = json.loads(data)
            msg_type = message.get('type')
            source = message.get('source', 'unknown')
            msg_data = message.get('data', {})
            
            self.log_debug(f"收到上行消息: {msg_type} from {websocket.remote_address} (source={source})")
            
            if source != 'unknown' and source != current_device_name:
                with self._connection_lock:
                    self._device_connections[source] = websocket
                self.log_info(f"设备 {source} 已注册连接")
                current_device_name = source
            
            # 处理不同类型的消息
            if msg_type == 'command_response':
                self._handle_command_response(message, source)
            elif msg_type == 'device_status':
                self._handle_device_status(message, source)
            elif msg_type == 'sensor_data':
                self._handle_sensor_data(message, source)
            elif msg_type == 'alert':
                self._handle_alert(message, source)
            elif msg_type == 'heartbeat_report':
                await self._handle_heartbeat_report(message, source, websocket)
            elif msg_type == 'camera_control_response':
                self._handle_camera_control_response(message, source)
            elif msg_type == 'camera_stream_started':
                self._handle_camera_stream_started(message, source)
            elif msg_type == 'camera_stream_stopped':
                self._handle_camera_stream_stopped(message, source)
            elif msg_type == 'subscribe_camera':
                camera_id = message.get('camera_id')
                if camera_id:
                    await self._subscribe_camera_stream(websocket, camera_id)
            elif msg_type == 'unsubscribe_camera':
                camera_id = message.get('camera_id')
                if camera_id:
                    await self._unsubscribe_camera_stream(websocket, camera_id)
            else:
                self.log_debug(f"未处理的上行消息类型: {msg_type} from {source}")
            
            return current_device_name
        
        except json.JSONDecodeError as e:
            self.log_warning(f"JSON解析失败: {e}")
            with self._stats_lock:
                self._stats['errors'] += 1
            return current_device_name
        except Exception as e:
            self.log_error(f"处理上行消息失败: {e}\n{traceback.format_exc()}")
            with self._stats_lock:
                self._stats['errors'] += 1
            return current_device_name
    
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
    
    # ==================== 摄像头消息处理 ====================
    
    async def _handle_camera_frame(self, data: bytes, websocket, 
                                   device_name: Optional[str]) -> Optional[str]:
        """处理摄像头二进制帧数据并转发给订阅者"""
        try:
            if len(data) < 20:
                self.log_warning(f"摄像头帧数据过短: {len(data)} bytes")
                return device_name
            
            # 解析头部
            magic = struct.unpack('>I', data[0:4])[0]
            
            if magic != 0x43414D46:  # 'CAMF'
                self.log_warning(f"无效的摄像头帧magic: {hex(magic)}")
                return device_name
            
            camera_id_len = struct.unpack('>I', data[4:8])[0]
            camera_id = data[8:8+camera_id_len].decode('utf-8')
            
            offset = 8 + camera_id_len
            frame_seq = struct.unpack('>I', data[offset:offset+4])[0]
            timestamp = struct.unpack('>d', data[offset+4:offset+12])[0]
            jpeg_data = data[offset+12:]
            
            # 更新流信息
            with self._camera_lock:
                if camera_id not in self._camera_streams:
                    self._camera_streams[camera_id] = {
                        'device': device_name,
                        'websocket': websocket,
                        'frame_count': 0,
                        'start_time': time.time()
                    }
                
                stream_info = self._camera_streams[camera_id]
                stream_info['frame_count'] += 1
                stream_info['last_frame_time'] = timestamp
                stream_info['last_frame_seq'] = frame_seq
                stream_info['last_frame_size'] = len(jpeg_data)
            
            # 缓存帧并转发给订阅者
            with self._frame_cache_lock:
                # 缓存最新帧（用于快照和新订阅者）
                self._camera_frame_cache[camera_id] = data
                
                # 获取该摄像头的所有订阅者
                subscribers = self._frame_subscribers.get(camera_id, set()).copy()
            
            # 转发帧给所有订阅者
            if subscribers:
                forward_count = 0
                failed_count = 0
                
                for subscriber in subscribers:
                    try:
                        await subscriber.send(data)
                        forward_count += 1
                    except Exception as e:
                        self.log_warning(f"转发帧失败: {subscriber.remote_address} - {e}")
                        failed_count += 1
                
                if forward_count > 0:
                    with self._stats_lock:
                        self._stats['frames_forwarded'] += forward_count
                    
                    self.log_debug(
                        f"帧已转发: {camera_id} seq={frame_seq} -> "
                        f"{forward_count}个订阅者 (失败:{failed_count})"
                    )
            
            # 发布事件（用于后端其他插件处理）
            self.publish_event(
                event_type=f"camera.frame.{camera_id}",
                data={
                    "camera_id": camera_id,
                    "device": device_name,
                    "frame_seq": frame_seq,
                    "timestamp": timestamp,
                    "frame_size": len(jpeg_data),
                    "subscriber_count": len(subscribers) if subscribers else 0
                },
                priority=EventPriority.NORMAL
            )
            
            with self._stats_lock:
                self._stats['messages_received'] += 1
                self._stats['frames_received'] += 1
            
            return device_name
            
        except Exception as e:
            self.log_error(f"处理摄像头帧失败: {e}\n{traceback.format_exc()}")
            return device_name
    
    def _handle_camera_control_response(self, message: Dict, source: str):
        """处理摄像头控制响应"""
        camera_id = message.get('camera_id')
        result = message.get('data', {})
        
        self.publish_event(
            event_type="camera.control.result",
            data={
                "camera_id": camera_id,
                "result": result,
                "source": source
            },
            priority=EventPriority.HIGH
        )
        
        self.log_info(f"摄像头控制响应: {camera_id} (source={source})")
    
    def _handle_camera_stream_started(self, message: Dict, source: str):
        """处理摄像头流启动通知"""
        camera_id = message.get('camera_id')
        
        with self._camera_lock:
            if camera_id not in self._camera_streams:
                self._camera_streams[camera_id] = {
                    'device': source,
                    'frame_count': 0,
                    'start_time': time.time()
                }
        
        self.publish_event(
            event_type=f"camera.stream.started",
            data={
                "camera_id": camera_id,
                "source": source
            },
            priority=EventPriority.NORMAL
        )
        
        self.log_info(f"摄像头流已启动: {camera_id} (source={source})")
    
    def _handle_camera_stream_stopped(self, message: Dict, source: str):
        """处理摄像头流停止通知"""
        camera_id = message.get('camera_id')
        
        with self._camera_lock:
            if camera_id in self._camera_streams:
                del self._camera_streams[camera_id]
        
        # 清理缓存
        with self._frame_cache_lock:
            if camera_id in self._camera_frame_cache:
                del self._camera_frame_cache[camera_id]
        
        self.publish_event(
            event_type=f"camera.stream.stopped",
            data={
                "camera_id": camera_id,
                "source": source
            },
            priority=EventPriority.NORMAL
        )
        
        self.log_info(f"摄像头流已停止: {camera_id} (source={source})")
    
    # ==================== 插件接口方法（增强版）====================
    
    def get_bridge_status(self, input_data: Any = None) -> Dict[str, Any]:
        """获取桥接器状态（增强版）"""
        with self._stats_lock:
            stats = self._stats.copy()
        
        with self._connection_lock:
            connected_devices = list(self._device_connections.keys())
            video_client_count = len(self._video_clients)
            detection_client_count = len(self._detection_clients)
            generic_client_count = len(self._generic_clients)
            total_client_count = len(self._all_clients)
        
        with self._camera_lock:
            camera_count = len(self._camera_streams)
        
        with self._frame_cache_lock:
            subscriber_count = sum(len(subs) for subs in self._frame_subscribers.values())
        
        uptime = time.time() - stats['start_time'] if stats['start_time'] > 0 else 0.0
        
        return {
            'running': self._running.is_set(),
            'listen_port': self._listen_port,
            'configured_devices': list(self._devices_config.keys()),
            'connected_devices': connected_devices,
            'connection_types': {
                'video_clients': video_client_count,
                'detection_clients': detection_client_count,
                'generic_clients': generic_client_count,
                'total_clients': total_client_count
            },
            'camera_stream_count': camera_count,
            'frame_subscriber_count': subscriber_count,
            'endpoints': ['/ingest_video', '/ingest_boxes', '/'],
            'statistics': {
                'uptime': uptime,
                'messages_sent': stats['messages_sent'],
                'messages_received': stats['messages_received'],
                'commands_forwarded': stats['commands_forwarded'],
                'frames_received': stats['frames_received'],
                'frames_forwarded': stats['frames_forwarded'],
                'detections_received': stats['detections_received'],
                'errors': stats['errors']
            }
        }
    
    def get_latest_frame(self, input_data: Any) -> Dict[str, Any]:
        """获取最新帧（用于快照）"""
        if not isinstance(input_data, dict):
            return {'success': False, 'message': '输入必须是字典格式'}
        
        camera_id = input_data.get('camera_id')
        if not camera_id:
            return {'success': False, 'message': '缺少camera_id参数'}
        
        with self._frame_cache_lock:
            frame_data = self._camera_frame_cache.get(camera_id)
        
        if frame_data:
            import base64
            return {
                'success': True,
                'camera_id': camera_id,
                'frame_data': base64.b64encode(frame_data).decode('utf-8'),
                'frame_size': len(frame_data)
            }
        else:
            return {
                'success': False,
                'message': f'没有缓存的帧: {camera_id}'
            }
    
    def get_camera_subscribers(self, input_data: Any = None) -> Dict[str, Any]:
        """获取摄像头订阅者信息"""
        with self._frame_cache_lock:
            subscriber_info = {}
            for camera_id, subscribers in self._frame_subscribers.items():
                subscriber_info[camera_id] = {
                    'subscriber_count': len(subscribers),
                    'subscribers': [str(ws.remote_address) for ws in subscribers]
                }
        
        return {
            'success': True,
            'subscribers': subscriber_info
        }
    
    def get_camera_streams(self, input_data: Any = None) -> Dict[str, Any]:
        """获取当前所有摄像头流信息"""
        with self._camera_lock:
            streams = {}
            current_time = time.time()
            
            for camera_id, info in self._camera_streams.items():
                uptime = current_time - info.get('start_time', current_time)
                fps = info.get('frame_count', 0) / uptime if uptime > 0 else 0
                
                streams[camera_id] = {
                    'device': info.get('device'),
                    'frame_count': info.get('frame_count', 0),
                    'uptime': uptime,
                    'fps': round(fps, 2),
                    'last_frame_seq': info.get('last_frame_seq'),
                    'last_frame_size': info.get('last_frame_size'),
                    'last_frame_time': info.get('last_frame_time')
                }
        
        with self._frame_cache_lock:
            for camera_id in streams:
                streams[camera_id]['subscriber_count'] = len(
                    self._frame_subscribers.get(camera_id, set())
                )
        
        return {
            'success': True,
            'stream_count': len(streams),
            'streams': streams
        }
    
    def get_connection_details(self, input_data: Any = None) -> Dict[str, Any]:
        """获取连接详情（新增方法）"""
        with self._connection_lock:
            connection_details = {
                'video_clients': [str(ws.remote_address) for ws in self._video_clients],
                'detection_clients': [str(ws.remote_address) for ws in self._detection_clients],
                'generic_clients': [str(ws.remote_address) for ws in self._generic_clients],
                'device_connections': {
                    device: str(ws.remote_address) for device, ws in self._device_connections.items()
                }
            }
        
        return {
            'success': True,
            'connection_details': connection_details,
            'total_connections': len(self._all_clients)
        }
    
    def stop_camera_stream(self, input_data: Any) -> Dict[str, Any]:
        """停止指定摄像头流"""
        if not isinstance(input_data, dict):
            return {'success': False, 'message': '输入必须是字典格式'}
        
        camera_id = input_data.get('camera_id')
        device_name = input_data.get('device_name', 'jetson_ai_camera')
        
        if not camera_id:
            return {'success': False, 'message': '缺少camera_id参数'}
        
        command_msg = {
            "type": "camera_control",
            "camera_id": camera_id,
            "action": "stop_stream",
            "timestamp": time.time()
        }
        
        with self._connection_lock:
            websocket = self._device_connections.get(device_name)
        
        if not websocket:
            return {'success': False, 'message': f'设备未连接: {device_name}'}
        
        try:
            data = json.dumps(command_msg)
            future = asyncio.run_coroutine_threadsafe(
                websocket.send(data),
                self._loop
            )
            future.result(timeout=5.0)
            
            return {
                'success': True,
                'message': f'停止命令已发送: {camera_id}'
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
            video_clients = len(self._video_clients)
            detection_clients = len(self._detection_clients)
            generic_clients = len(self._generic_clients)
        
        with self._camera_lock:
            cameras = list(self._camera_streams.keys())
        
        return {
            'statistics': stats,
            'connected_devices': devices,
            'connection_counts': {
                'video': video_clients,
                'detection': detection_clients,
                'generic': generic_clients
            },
            'camera_streams': cameras,
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