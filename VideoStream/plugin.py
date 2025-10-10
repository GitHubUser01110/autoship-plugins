"""
视频流插件 - UDP版本 (视频流 + 检测框流)

核心功能：
1. ✅ UDP接收视频帧（高效单向传输）
2. ✅ UDP接收检测框数据
3. ✅ 异步帧发送给订阅者（WebSocket）
4. ✅ 帧队列管理（只保留最新1帧）
5. ✅ 流量控制和丢帧策略
6. ✅ 性能统计监控
"""

import asyncio
import websockets
import json
import socket
import threading
import time
import struct
import traceback
from typing import Any, Dict, Optional, Set
from collections import defaultdict, deque
from app.usv.plugin_base import Plugin, Response, PluginState
from app.usv.event_bus import EventData, EventPriority


# ==================== 视频帧处理器 ====================

class VideoFrameHandler:
    """
    视频帧处理器 - 核心优化组件
    
    功能：
    - 每个订阅者维护独立的帧队列（只保留最新1帧）
    - 异步发送协程（不阻塞接收）
    - 流量控制（限制发送速率）
    - 自动丢弃过期帧
    """
    
    def __init__(self, target_fps: int = 20, max_queue_size: int = 1):
        """
        Args:
            target_fps: 目标发送帧率（建议15-20）
            max_queue_size: 每个订阅者的队列大小（建议1）
        """
        self.target_fps = target_fps
        self.frame_interval = 1.0 / target_fps  # 帧间隔
        self.max_queue_size = max_queue_size
        
        # 每个订阅者的帧队列（只保留最新帧）
        self.frame_queues: Dict[websockets.WebSocketServerProtocol, deque] = {}
        
        # 发送任务管理
        self.send_tasks: Dict[websockets.WebSocketServerProtocol, asyncio.Task] = {}
        
        # 统计信息
        self.stats = {
            'frames_received': 0,      # 接收到的总帧数
            'frames_enqueued': 0,      # 成功入队的帧数
            'frames_dropped': 0,       # 被丢弃的旧帧数
            'frames_sent': 0,          # 成功发送的帧数
            'frames_failed': 0,        # 发送失败的帧数
            'total_bytes_sent': 0,     # 发送的总字节数
            'active_subscribers': 0    # 活跃订阅者数
        }
        self.stats_lock = threading.Lock()
    
    async def add_frame(self, frame_data: bytes, camera_id: str, 
                       subscribers: Set[websockets.WebSocketServerProtocol]):
        """
        添加新帧到订阅者队列
        
        关键优化：只保留最新帧，旧帧自动丢弃
        """
        with self.stats_lock:
            self.stats['frames_received'] += 1
        
        for subscriber in subscribers:
            try:
                # 首次订阅：创建队列和发送任务
                if subscriber not in self.frame_queues:
                    self.frame_queues[subscriber] = deque(maxlen=self.max_queue_size)
                    
                    # 启动独立的发送协程
                    task = asyncio.create_task(
                        self._send_worker(subscriber, camera_id)
                    )
                    self.send_tasks[subscriber] = task
                    
                    with self.stats_lock:
                        self.stats['active_subscribers'] += 1
                
                queue = self.frame_queues[subscriber]
                
                # 队列满时，旧帧会被自动丢弃（deque的maxlen特性）
                if len(queue) >= self.max_queue_size:
                    with self.stats_lock:
                        self.stats['frames_dropped'] += 1
                
                # 入队新帧
                queue.append({
                    'data': frame_data,
                    'timestamp': time.time(),
                    'camera_id': camera_id
                })
                
                with self.stats_lock:
                    self.stats['frames_enqueued'] += 1
                    
            except Exception as e:
                print(f"[VideoHandler] 添加帧失败: {subscriber.remote_address} - {e}")
    
    async def _send_worker(self, subscriber: websockets.WebSocketServerProtocol, 
                          camera_id: str):
        """
        发送工作协程 - 每个订阅者一个独立协程
        
        关键优化：
        - 非阻塞：不影响其他订阅者
        - 流量控制：限制发送速率
        - 只发最新帧：自动跳过过期帧
        """
        queue = self.frame_queues[subscriber]
        last_send_time = 0
        consecutive_failures = 0
        MAX_FAILURES = 5  # 连续失败5次后断开
        
        try:
            while True:
                # 等待队列有数据
                while len(queue) == 0:
                    await asyncio.sleep(0.001)  # 1ms检查一次
                    
                    # 检测连接是否已关闭
                    if subscriber.closed:
                        raise ConnectionResetError("WebSocket已关闭")
                
                # 流量控制：限制发送速率
                now = time.time()
                elapsed = now - last_send_time
                if elapsed < self.frame_interval:
                    await asyncio.sleep(self.frame_interval - elapsed)
                
                # 只取最新帧，丢弃队列中的旧帧
                frame_info = queue.pop()
                queue.clear()  # 清空剩余旧帧
                
                frame_data = frame_info['data']
                frame_age = time.time() - frame_info['timestamp']
                
                # 跳过过期帧（超过1秒）
                if frame_age > 1.0:
                    with self.stats_lock:
                        self.stats['frames_dropped'] += 1
                    continue
                
                # 发送帧
                try:
                    await subscriber.send(frame_data)
                    
                    with self.stats_lock:
                        self.stats['frames_sent'] += 1
                        self.stats['total_bytes_sent'] += len(frame_data)
                    
                    last_send_time = time.time()
                    consecutive_failures = 0  # 重置失败计数
                    
                except Exception as e:
                    consecutive_failures += 1
                    
                    with self.stats_lock:
                        self.stats['frames_failed'] += 1
                    
                    print(f"[VideoHandler] 发送失败 [{consecutive_failures}/{MAX_FAILURES}]: "
                          f"{subscriber.remote_address} - {e}")
                    
                    # 连续失败太多次，断开连接
                    if consecutive_failures >= MAX_FAILURES:
                        print(f"[VideoHandler] 连续失败过多，终止发送: {subscriber.remote_address}")
                        break
                    
                    await asyncio.sleep(0.1)  # 失败后短暂延迟
        
        except (asyncio.CancelledError, ConnectionResetError) as e:
            print(f"[VideoHandler] 发送任务已结束: {subscriber.remote_address} - {type(e).__name__}")
        
        except Exception as e:
            print(f"[VideoHandler] 发送任务异常: {subscriber.remote_address}\n{traceback.format_exc()}")
        
        finally:
            # 清理资源
            await self._cleanup_subscriber(subscriber)
    
    async def _cleanup_subscriber(self, subscriber: websockets.WebSocketServerProtocol):
        """清理订阅者资源"""
        self.frame_queues.pop(subscriber, None)
        self.send_tasks.pop(subscriber, None)
        
        with self.stats_lock:
            self.stats['active_subscribers'] = len(self.frame_queues)
        
        print(f"[VideoHandler] 已清理订阅者: {subscriber.remote_address}")
    
    async def remove_subscriber(self, subscriber: websockets.WebSocketServerProtocol):
        """主动移除订阅者"""
        if subscriber in self.send_tasks:
            task = self.send_tasks[subscriber]
            task.cancel()
            
            try:
                await task
            except asyncio.CancelledError:
                pass
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self.stats_lock:
            stats = self.stats.copy()
        
        # 计算发送速率
        if stats['frames_sent'] > 0:
            stats['avg_frame_size'] = stats['total_bytes_sent'] / stats['frames_sent']
        else:
            stats['avg_frame_size'] = 0
        
        # 丢帧率
        total_frames = stats['frames_received']
        if total_frames > 0:
            stats['drop_rate'] = stats['frames_dropped'] / total_frames * 100
        else:
            stats['drop_rate'] = 0.0
        
        return stats


# ==================== 视频流插件主类 ====================

class VideoStreamPlugin(Plugin):
    """视频流插件 (UDP接收 + WebSocket分发)"""

    VERSION = '1.0.0'
    MIN_COMPATIBLE_VERSION = '1.0.0'

    def __init__(self, plugin_id: str, plugin_manager):
        super().__init__(plugin_id, plugin_manager)

        # 配置参数
        self._udp_video_port = 14000  # UDP视频流端口
        self._udp_detection_port = 14001  # UDP检测框端口
        self._ws_port = 14002  # WebSocket订阅端口

        # UDP套接字
        self._video_socket: Optional[socket.socket] = None
        self._detection_socket: Optional[socket.socket] = None
        
        # WebSocket服务器
        self._ws_server = None
        self._ws_clients: Set[websockets.WebSocketServerProtocol] = set()
        self._ws_lock = threading.Lock()

        # 视频帧处理器（核心优化）
        self._video_handler = VideoFrameHandler(
            target_fps=20,      # 目标发送帧率
            max_queue_size=1    # 队列只保留1帧
        )

        # 线程管理
        self._video_thread: Optional[threading.Thread] = None
        self._detection_thread: Optional[threading.Thread] = None
        self._ws_thread: Optional[threading.Thread] = None

        # 异步事件循环
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # 运行状态
        self._running = threading.Event()
        self._ws_ready = threading.Event()

        # 摄像头流管理
        self._camera_streams: Dict[str, Dict[str, Any]] = {}
        self._camera_lock = threading.Lock()
        
        # 帧缓存和订阅管理
        self._camera_frame_cache: Dict[str, bytes] = {}
        self._frame_subscribers: Dict[str, Set[websockets.WebSocketServerProtocol]] = defaultdict(set)
        self._subscriber_cameras: Dict[websockets.WebSocketServerProtocol, Set[str]] = defaultdict(set)
        self._frame_cache_lock = threading.Lock()

        # 统计信息
        self._stats = {
            'frames_received': 0,
            'detections_received': 0,
            'errors': 0,
            'start_time': 0.0
        }
        self._stats_lock = threading.Lock()

        self.log_info("视频流插件初始化完成 (UDP接收 v1.0.0)")

    # ==================== 插件生命周期方法 ====================

    def _handle_install(self) -> Response:
        """安装插件"""
        self.log_info("正在安装视频流插件...")

        try:
            self._udp_video_port = int(self.get_config('udp_video_port', 14000))
            self._udp_detection_port = int(self.get_config('udp_detection_port', 14001))
            self._ws_port = int(self.get_config('ws_port', 14002))

            if not all(1024 <= p <= 65535 for p in [self._udp_video_port, self._udp_detection_port, self._ws_port]):
                return Response(success=False, data="端口号必须在1024-65535范围内")

            self.log_info(f"配置已加载: video_port={self._udp_video_port}, "
                         f"detection_port={self._udp_detection_port}, ws_port={self._ws_port}")
            self.log_info("视频流插件安装成功")
            return Response(success=True, data="安装成功")

        except Exception as e:
            self.log_error(f"安装插件失败: {e}\n{traceback.format_exc()}")
            return Response(success=False, data=str(e))

    def _handle_enable(self) -> Response:
        """启用插件"""
        self.log_info("正在启用视频流插件...")

        try:
            self._running.set()
            self._ws_ready.clear()

            # 启动UDP视频接收线程
            self._video_thread = threading.Thread(
                target=self._run_video_receiver,
                name=f"{self.plugin_id}-video-udp",
                daemon=True
            )
            self._video_thread.start()

            # 启动UDP检测框接收线程
            self._detection_thread = threading.Thread(
                target=self._run_detection_receiver,
                name=f"{self.plugin_id}-detection-udp",
                daemon=True
            )
            self._detection_thread.start()

            # 启动WebSocket服务器线程
            self._ws_thread = threading.Thread(
                target=self._run_websocket_server,
                name=f"{self.plugin_id}-ws-server",
                daemon=True
            )
            self._ws_thread.start()

            # 等待WebSocket服务器就绪
            if not self._ws_ready.wait(timeout=10.0):
                raise Exception("WebSocket服务器启动超时")

            self.log_info(f"✓ UDP视频接收: 0.0.0.0:{self._udp_video_port}")
            self.log_info(f"✓ UDP检测框接收: 0.0.0.0:{self._udp_detection_port}")
            self.log_info(f"✓ WebSocket订阅服务: ws://0.0.0.0:{self._ws_port}")
            self.log_info("  - 优化已启用: 异步发送 + 丢帧策略 + 流量控制")

            with self._stats_lock:
                self._stats['start_time'] = time.time()

            self.publish_event(
                event_type="videostream.started",
                data={
                    'plugin_id': self.plugin_id,
                    'udp_video_port': self._udp_video_port,
                    'udp_detection_port': self._udp_detection_port,
                    'ws_port': self._ws_port,
                    'protocol': 'udp+websocket',
                    'optimizations': ['async_send', 'frame_drop', 'rate_limit']
                },
                priority=EventPriority.NORMAL
            )

            self.log_info("视频流插件启用成功")
            return Response(success=True, data="启用成功")

        except Exception as e:
            self.log_error(f"启用插件失败: {e}\n{traceback.format_exc()}")
            self._cleanup()
            return Response(success=False, data=str(e))

    def _handle_disable(self) -> Response:
        """禁用插件"""
        self.log_info("正在禁用视频流插件...")

        try:
            self._running.clear()

            # 关闭UDP套接字
            if self._video_socket:
                try:
                    self._video_socket.close()
                except Exception as e:
                    self.log_warning(f"关闭视频UDP套接字时出错: {e}")
            
            if self._detection_socket:
                try:
                    self._detection_socket.close()
                except Exception as e:
                    self.log_warning(f"关闭检测框UDP套接字时出错: {e}")

            # 关闭WebSocket服务器
            if self._loop and self._ws_server:
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        self._shutdown_ws_server(),
                        self._loop
                    )
                    future.result(timeout=5.0)
                except Exception as e:
                    self.log_warning(f"关闭WebSocket服务器时出错: {e}")

            # 等待线程结束
            for thread in [self._video_thread, self._detection_thread, self._ws_thread]:
                if thread and thread.is_alive():
                    thread.join(timeout=5.0)

            self._cleanup()

            self.publish_event(
                event_type="videostream.stopped",
                data={'plugin_id': self.plugin_id},
                priority=EventPriority.NORMAL
            )

            self.log_info("视频流插件禁用成功")
            return Response(success=True, data="禁用成功")

        except Exception as e:
            self.log_error(f"禁用插件失败: {e}\n{traceback.format_exc()}")
            return Response(success=False, data=str(e))

    def _handle_config_update(self, old_config: Dict, new_config: Dict) -> Response:
        """处理配置更新"""
        self.log_info("配置更新需要重启插件以生效")
        return Response(success=True, data="配置已保存，需要重启插件以生效")

    def _cleanup(self):
        """清理资源"""
        try:
            with self._ws_lock:
                self._ws_clients.clear()
            
            with self._camera_lock:
                self._camera_streams.clear()
            
            with self._frame_cache_lock:
                self._camera_frame_cache.clear()
                self._frame_subscribers.clear()
                self._subscriber_cameras.clear()
                
        except Exception as e:
            self.log_warning(f"清理资源时出错: {e}")

    # ==================== UDP接收器 ====================

    def _run_video_receiver(self):
        """运行UDP视频帧接收器"""
        try:
            self._video_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._video_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._video_socket.bind(('0.0.0.0', self._udp_video_port))
            self._video_socket.settimeout(1.0)
            
            self.log_info(f"UDP视频接收器已启动: 0.0.0.0:{self._udp_video_port}")
            
            while self._running.is_set():
                try:
                    data, addr = self._video_socket.recvfrom(65535)
                    self._handle_video_frame(data)
                    
                except socket.timeout:
                    continue
                except Exception as e:
                    if self._running.is_set():
                        self.log_error(f"接收视频帧错误: {e}")
                        with self._stats_lock:
                            self._stats['errors'] += 1
            
        except Exception as e:
            self.log_error(f"UDP视频接收器错误: {e}\n{traceback.format_exc()}")
        finally:
            if self._video_socket:
                self._video_socket.close()
            self.log_info("UDP视频接收器已停止")

    def _run_detection_receiver(self):
        """运行UDP检测框接收器"""
        try:
            self._detection_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._detection_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._detection_socket.bind(('0.0.0.0', self._udp_detection_port))
            self._detection_socket.settimeout(1.0)
            
            self.log_info(f"UDP检测框接收器已启动: 0.0.0.0:{self._udp_detection_port}")
            
            while self._running.is_set():
                try:
                    data, addr = self._detection_socket.recvfrom(65535)
                    self._handle_detection_data(data.decode('utf-8'))
                    
                except socket.timeout:
                    continue
                except Exception as e:
                    if self._running.is_set():
                        self.log_error(f"接收检测框错误: {e}")
                        with self._stats_lock:
                            self._stats['errors'] += 1
            
        except Exception as e:
            self.log_error(f"UDP检测框接收器错误: {e}\n{traceback.format_exc()}")
        finally:
            if self._detection_socket:
                self._detection_socket.close()
            self.log_info("UDP检测框接收器已停止")

    def _handle_video_frame(self, data: bytes):
        """处理视频帧数据"""
        try:
            if len(data) < 20:
                return
            
            # 解析头部
            magic = struct.unpack('>I', data[0:4])[0]
            if magic != 0x43414D46:  # 'CAMF'
                return
            
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
                        'frame_count': 0,
                        'start_time': time.time()
                    }
                
                stream_info = self._camera_streams[camera_id]
                stream_info['frame_count'] += 1
                stream_info['last_frame_time'] = timestamp
                stream_info['last_frame_seq'] = frame_seq
                stream_info['last_frame_size'] = len(jpeg_data)
            
            # 缓存最新帧
            with self._frame_cache_lock:
                self._camera_frame_cache[camera_id] = data
                subscribers = self._frame_subscribers.get(camera_id, set()).copy()
            
            # 使用VideoFrameHandler异步发送
            if subscribers and self._loop:
                asyncio.run_coroutine_threadsafe(
                    self._video_handler.add_frame(data, camera_id, subscribers),
                    self._loop
                )
            
            # 发布事件
            self.publish_event(
                event_type=f"camera.frame.{camera_id}",
                data={
                    "camera_id": camera_id,
                    "frame_seq": frame_seq,
                    "timestamp": timestamp,
                    "frame_size": len(jpeg_data),
                    "subscriber_count": len(subscribers) if subscribers else 0
                },
                priority=EventPriority.NORMAL
            )
            
            with self._stats_lock:
                self._stats['frames_received'] += 1
                    
        except Exception as e:
            self.log_error(f"处理视频帧失败: {e}\n{traceback.format_exc()}")

    def _handle_detection_data(self, data: str):
        """处理检测框数据"""
        try:
            with self._stats_lock:
                self._stats['detections_received'] += 1

            message = json.loads(data)
            camera_id = message.get('camera_id')
            
            if camera_id:
                with self._frame_cache_lock:
                    subscribers = self._frame_subscribers.get(camera_id, set()).copy()
                
                if subscribers and self._loop:
                    forward_msg = json.dumps({
                        "type": "detection_data",
                        "camera_id": camera_id,
                        "frame_id": message.get('id'),
                        "timestamp": message.get('ts'),
                        "detections": message.get('det', []),
                        "scale": message.get('scale', 1.0),
                        "w": message.get('w'),
                        "h": message.get('h')
                    })
                    
                    for subscriber in subscribers:
                        try:
                            asyncio.run_coroutine_threadsafe(
                                subscriber.send(forward_msg),
                                self._loop
                            )
                        except Exception as e:
                            self.log_warning(f"转发检测框失败: {subscriber.remote_address} - {e}")

            self.publish_event(
                event_type="detection.boxes",
                data={
                    "camera_id": message.get('camera_id'),
                    "detections": message.get('det', []),
                    "frame_id": message.get('id'),
                    "timestamp": message.get('ts'),
                    "detection_count": len(message.get('det', []))
                },
                priority=EventPriority.NORMAL
            )

        except json.JSONDecodeError as e:
            self.log_warning(f"检测框JSON解析失败: {e}")
        except Exception as e:
            self.log_error(f"处理检测框失败: {e}\n{traceback.format_exc()}")

    # ==================== WebSocket服务器 ====================

    def _run_websocket_server(self):
        """运行WebSocket服务器"""
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            
            self._loop.run_until_complete(self._start_ws_server())
            
        except Exception as e:
            self.log_error(f"WebSocket服务器线程错误: {e}\n{traceback.format_exc()}")
            self._ws_ready.set()
        finally:
            if self._loop:
                try:
                    self._loop.close()
                except Exception as e:
                    self.log_warning(f"关闭事件循环时出错: {e}")

    async def _start_ws_server(self):
        """启动WebSocket服务器"""
        try:
            async with websockets.serve(
                self._handle_ws_client,
                '0.0.0.0',
                self._ws_port,
                ping_interval=30,
                ping_timeout=10,
                max_size=10 * 1024 * 1024,
                write_limit=5 * 1024 * 1024
            ) as server:
                self._ws_server = server
                
                self.log_info(f"✓ WebSocket订阅服务已启动: ws://0.0.0.0:{self._ws_port}")
                
                self._ws_ready.set()
                
                stop_future = asyncio.Future()
                
                async def check_running():
                    while self._running.is_set():
                        await asyncio.sleep(1)
                    stop_future.set_result(None)
                
                asyncio.create_task(check_running())
                await stop_future
                
        except Exception as e:
            self.log_error(f"WebSocket服务器启动失败: {e}\n{traceback.format_exc()}")
            self._ws_ready.set()

    async def _shutdown_ws_server(self):
        """关闭WebSocket服务器"""
        if self._ws_server:
            self._ws_server.close()
            await self._ws_server.wait_closed()

    async def _handle_ws_client(self, websocket):
        """处理WebSocket客户端"""
        client_addr = websocket.remote_address
        self.log_info(f"新订阅客户端: {client_addr}")

        with self._ws_lock:
            self._ws_clients.add(websocket)

        try:
            async for message in websocket:
                if isinstance(message, str):
                    await self._handle_ws_message(message, websocket)

        except websockets.exceptions.ConnectionClosed:
            self.log_info(f"订阅客户端断开: {client_addr}")
        except Exception as e:
            self.log_error(f"处理订阅客户端错误: {e}\n{traceback.format_exc()}")
        finally:
            await self._cleanup_ws_client(websocket)

    async def _handle_ws_message(self, data: str, websocket):
        """处理WebSocket消息"""
        try:
            message = json.loads(data)
            msg_type = message.get('type')

            if msg_type == 'subscribe_camera':
                camera_id = message.get('camera_id')
                if camera_id:
                    await self._subscribe_camera(websocket, camera_id)
            elif msg_type == 'unsubscribe_camera':
                camera_id = message.get('camera_id')
                if camera_id:
                    await self._unsubscribe_camera(websocket, camera_id)

        except json.JSONDecodeError as e:
            self.log_warning(f"WebSocket消息JSON解析失败: {e}")
        except Exception as e:
            self.log_error(f"处理WebSocket消息失败: {e}\n{traceback.format_exc()}")

    async def _subscribe_camera(self, websocket, camera_id: str):
        """订阅摄像头流"""
        with self._frame_cache_lock:
            self._frame_subscribers[camera_id].add(websocket)
            self._subscriber_cameras[websocket].add(camera_id)
        
        self.log_info(f"客户端订阅摄像头: {camera_id} ({websocket.remote_address})")
        
        ack_msg = {
            "type": "subscribe_ack",
            "camera_id": camera_id,
            "status": "success",
            "timestamp": time.time()
        }
        
        try:
            await websocket.send(json.dumps(ack_msg))
            
            # 发送缓存帧
            if camera_id in self._camera_frame_cache:
                subscribers = {websocket}
                await self._video_handler.add_frame(
                    self._camera_frame_cache[camera_id],
                    camera_id,
                    subscribers
                )
                
        except Exception as e:
            self.log_error(f"发送订阅确认失败: {e}")

    async def _unsubscribe_camera(self, websocket, camera_id: str):
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
        
        # 清理视频处理器
        await self._video_handler.remove_subscriber(websocket)
        
        self.log_info(f"客户端取消订阅摄像头: {camera_id} ({websocket.remote_address})")
        
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

    async def _cleanup_ws_client(self, websocket):
        """清理WebSocket客户端"""
        with self._ws_lock:
            self._ws_clients.discard(websocket)
        
        with self._frame_cache_lock:
            subscribed_cameras = self._subscriber_cameras.get(websocket, set()).copy()
            
            for camera_id in subscribed_cameras:
                if camera_id in self._frame_subscribers:
                    self._frame_subscribers[camera_id].discard(websocket)
                    if not self._frame_subscribers[camera_id]:
                        del self._frame_subscribers[camera_id]
            
            if websocket in self._subscriber_cameras:
                del self._subscriber_cameras[websocket]
        
        # 清理视频处理器
        await self._video_handler.remove_subscriber(websocket)
        
        if subscribed_cameras:
            self.log_info(f"清理订阅客户端: {websocket.remote_address}, 订阅的摄像头: {subscribed_cameras}")

    # ==================== 插件接口方法 ====================

    def get_stream_status(self, input_data: Any = None) -> Dict[str, Any]:
        """获取视频流状态"""
        with self._stats_lock:
            stats = self._stats.copy()
        
        with self._ws_lock:
            ws_client_count = len(self._ws_clients)
        
        with self._camera_lock:
            camera_count = len(self._camera_streams)
        
        with self._frame_cache_lock:
            subscriber_count = sum(len(subs) for subs in self._frame_subscribers.values())
        
        uptime = time.time() - stats['start_time'] if stats['start_time'] > 0 else 0.0
        
        # 获取视频处理器统计
        video_handler_stats = self._video_handler.get_stats()
        
        return {
            'running': self._running.is_set(),
            'udp_video_port': self._udp_video_port,
            'udp_detection_port': self._udp_detection_port,
            'ws_port': self._ws_port,
            'protocol': 'udp+websocket',
            'camera_stream_count': camera_count,
            'ws_client_count': ws_client_count,
            'frame_subscriber_count': subscriber_count,
            'statistics': {
                'uptime': uptime,
                'frames_received': stats['frames_received'],
                'frames_forwarded': video_handler_stats['frames_sent'],
                'detections_received': stats['detections_received'],
                'errors': stats['errors']
            },
            'video_handler_stats': video_handler_stats
        }

    def get_performance_report(self, input_data: Any = None) -> Dict[str, Any]:
        """获取性能报告"""
        video_stats = self._video_handler.get_stats()
        
        with self._stats_lock:
            base_stats = self._stats.copy()
        
        uptime = time.time() - base_stats['start_time'] if base_stats['start_time'] > 0 else 1.0
        
        return {
            'success': True,
            'performance': {
                'video_processing': {
                    'frames_received': video_stats['frames_received'],
                    'frames_sent': video_stats['frames_sent'],
                    'frames_dropped': video_stats['frames_dropped'],
                    'frames_failed': video_stats['frames_failed'],
                    'drop_rate_percent': round(video_stats['drop_rate'], 2),
                    'avg_frame_size_kb': round(video_stats['avg_frame_size'] / 1024, 2),
                    'throughput_mbps': round(video_stats['total_bytes_sent'] / uptime / 1024 / 1024 * 8, 2),
                    'active_subscribers': video_stats['active_subscribers']
                },
                'overall': {
                    'uptime_seconds': round(uptime, 2),
                    'frames_received': base_stats['frames_received'],
                    'detections_received': base_stats['detections_received'],
                    'errors': base_stats['errors']
                }
            },
            'recommendations': self._get_performance_recommendations(video_stats)
        }

    def _get_performance_recommendations(self, video_stats: Dict) -> list:
        """生成性能优化建议"""
        recommendations = []
        
        if video_stats['drop_rate'] > 30:
            recommendations.append({
                'level': 'warning',
                'message': f"丢帧率较高 ({video_stats['drop_rate']:.1f}%)，建议降低发送端帧率或质量"
            })
        
        if video_stats['frames_failed'] > video_stats['frames_sent'] * 0.1:
            recommendations.append({
                'level': 'error',
                'message': "发送失败率高，检查网络连接或客户端处理速度"
            })
        
        if video_stats['avg_frame_size'] > 100 * 1024:  # 100KB
            recommendations.append({
                'level': 'info',
                'message': f"平均帧大小较大 ({video_stats['avg_frame_size']/1024:.1f}KB)，考虑降低JPEG质量"
            })
        
        if not recommendations:
            recommendations.append({
                'level': 'success',
                'message': "性能良好，无需优化"
            })
        
        return recommendations

    def get_camera_streams(self, input_data: Any = None) -> Dict[str, Any]:
        """获取当前所有摄像头流信息"""
        with self._camera_lock:
            streams = {}
            current_time = time.time()
            
            for camera_id, info in self._camera_streams.items():
                uptime = current_time - info.get('start_time', current_time)
                fps = info.get('frame_count', 0) / uptime if uptime > 0 else 0
                
                streams[camera_id] = {
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

    # ==================== 状态保存与恢复 ====================

    def _save_custom_state(self) -> Optional[Dict[str, Any]]:
        """保存自定义状态"""
        with self._stats_lock:
            stats = self._stats.copy()
        
        with self._camera_lock:
            cameras = list(self._camera_streams.keys())
        
        return {
            'statistics': stats,
            'camera_streams': cameras
        }

    def _restore_custom_state(self, custom_state: Dict[str, Any]) -> None:
        """恢复自定义状态"""
        if 'statistics' in custom_state:
            with self._stats_lock:
                self._stats.update(custom_state['statistics'])


__plugin_class__ = VideoStreamPlugin