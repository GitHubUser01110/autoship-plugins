"""
数据生成插件 - plugin.py

生成模拟传感器数据的插件，支持温度、湿度、GPS等多种数据类型
"""

import time
import random
import threading
import math
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone

from plugins.foundation.plugin_base import Plugin, Response


class DataGeneratorPlugin(Plugin):
    """数据生成器插件"""
    
    VERSION = '1.0.0'
    MIN_COMPATIBLE_VERSION = '1.0.0'
    
    def __init__(self, plugin_id: str, plugin_base):
        super().__init__(plugin_id, plugin_base)
        
        # 数据生成相关
        self._generation_thread: Optional[threading.Thread] = None
        self._stop_generation = threading.Event()
        self._data_lock = threading.Lock()
        
        # 数据存储
        self._latest_data: Dict[str, Any] = {}
        self._data_history: List[Dict[str, Any]] = []
        self._max_history_size = 1000
        
        # 模拟基础数据
        self._base_temperature = 25.0  # 基础温度
        self._base_humidity = 60.0     # 基础湿度
        self._base_latitude = 31.2304  # 基础纬度（上海）
        self._base_longitude = 121.4737 # 基础经度（上海）
        self._base_speed = 0.0         # 基础速度
        self._battery_level = 100.0    # 电池电量
        
        # 时间相关
        self._start_time = time.time()
        self._generation_count = 0
        
        self.logger.info("数据生成插件初始化完成")
    
    # ==================== 生命周期方法 ====================
    
    def _handle_enable(self) -> Response:
        """插件启用时调用"""
        try:
            # 订阅系统事件
            self.subscribe_event("system.startup", self._handle_system_startup)
            self.subscribe_event("system.shutdown", self._handle_system_shutdown)
            
            # 自动开始数据生成
            self.start_generation()
            
            self.log_info("数据生成插件已启用")
            return Response(success=True, data="数据生成插件启用成功")
            
        except Exception as e:
            self.log_error(f"启用插件失败: {e}")
            return Response(success=False, data=str(e))
    
    def _handle_disable(self) -> Response:
        """插件禁用时调用"""
        try:
            # 停止数据生成
            self.stop_generation()
            
            # 清理临时状态，保留持久化数据
            self.clear_transient_state()
            
            self.log_info("数据生成插件已禁用")
            return Response(success=True, data="数据生成插件禁用成功")
            
        except Exception as e:
            self.log_error(f"禁用插件失败: {e}")
            return Response(success=False, data=str(e))
    
    def _handle_config_update(self, old_config: Dict, new_config: Dict) -> Response:
        """配置更新时调用"""
        try:
            # 如果生成间隔改变，重启数据生成
            old_interval = old_config.get('generation_interval', 5.0)
            new_interval = new_config.get('generation_interval', 5.0)
            
            if old_interval != new_interval:
                self.log_info(f"生成间隔从 {old_interval}s 改为 {new_interval}s")
                if self._generation_thread and self._generation_thread.is_alive():
                    self.stop_generation()
                    time.sleep(0.1)  # 短暂等待
                    self.start_generation()
            
            return Response(success=True, data="配置更新成功")
            
        except Exception as e:
            self.log_error(f"配置更新失败: {e}")
            return Response(success=False, data=str(e))
    
    # ==================== 插件接口方法 ====================
    
    def get_latest_data(self, input_data: Any = None) -> Dict[str, Any]:
        """获取最新生成的数据"""
        with self._data_lock:
            return self._latest_data.copy()
    
    def get_data_history(self, input_data: Any = None) -> List[Dict[str, Any]]:
        """获取数据历史记录"""
        limit = 100  # 默认返回最近100条
        if isinstance(input_data, dict):
            limit = input_data.get('limit', 100)
        elif isinstance(input_data, int):
            limit = input_data
        
        with self._data_lock:
            return self._data_history[-limit:].copy() if self._data_history else []
    
    def start_generation(self, input_data: Any = None) -> str:
        """开始数据生成"""
        try:
            if self._generation_thread and self._generation_thread.is_alive():
                return "数据生成已在运行中"
            
            self._stop_generation.clear()
            self._generation_thread = threading.Thread(
                target=self._generation_worker,
                name=f"DataGen-{self.plugin_id}",
                daemon=True
            )
            self._generation_thread.start()
            
            self.log_info("数据生成已开始")
            return "数据生成已开始"
            
        except Exception as e:
            self.log_error(f"启动数据生成失败: {e}")
            return f"启动失败: {e}"
    
    def stop_generation(self, input_data: Any = None) -> str:
        """停止数据生成"""
        try:
            self._stop_generation.set()
            
            if self._generation_thread and self._generation_thread.is_alive():
                self._generation_thread.join(timeout=2.0)
            
            self.log_info("数据生成已停止")
            return "数据生成已停止"
            
        except Exception as e:
            self.log_error(f"停止数据生成失败: {e}")
            return f"停止失败: {e}"
    
    # ==================== 数据生成核心逻辑 ====================
    
    def _generation_worker(self):
        """数据生成工作线程"""
        self.log_info("数据生成工作线程启动")
        
        while not self._stop_generation.is_set():
            try:
                # 获取配置
                interval = self.get_config('generation_interval', 5.0)
                data_types = self.get_config('data_types', ['temperature', 'humidity', 'gps'])
                enable_noise = self.get_config('enable_noise', True)
                
                # 生成数据
                generated_data = self._generate_sensor_data(data_types, enable_noise)
                
                # 存储数据
                with self._data_lock:
                    self._latest_data = generated_data
                    self._data_history.append(generated_data)
                    
                    # 限制历史记录大小
                    if len(self._data_history) > self._max_history_size:
                        self._data_history = self._data_history[-self._max_history_size:]
                
                # 发布事件
                self._publish_data_events(generated_data)
                
                # 更新生成计数
                self._generation_count += 1
                
                # 等待下次生成
                self._stop_generation.wait(timeout=interval)
                
            except Exception as e:
                self.log_error(f"数据生成异常: {e}")
                self._stop_generation.wait(timeout=1.0)  # 错误时短暂等待
        
        self.log_info("数据生成工作线程结束")
    
    def _generate_sensor_data(self, data_types: List[str], enable_noise: bool) -> Dict[str, Any]:
        """生成传感器数据"""
        current_time = time.time()
        time_offset = current_time - self._start_time
        
        data = {
            'timestamp': current_time,
            'generation_count': self._generation_count,
            'plugin_id': self.plugin_id
        }
        
        for data_type in data_types:
            if data_type == 'temperature':
                data['temperature'] = self._generate_temperature(time_offset, enable_noise)
            elif data_type == 'humidity':
                data['humidity'] = self._generate_humidity(time_offset, enable_noise)
            elif data_type == 'gps':
                gps_data = self._generate_gps(time_offset, enable_noise)
                data.update(gps_data)
            elif data_type == 'speed':
                data['speed'] = self._generate_speed(time_offset, enable_noise)
            elif data_type == 'battery':
                data['battery_level'] = self._generate_battery_level(time_offset, enable_noise)
        
        return data
    
    def _generate_temperature(self, time_offset: float, enable_noise: bool) -> float:
        """生成温度数据"""
        # 模拟日温度变化（24小时周期）
        daily_cycle = math.sin(time_offset * 2 * math.pi / 86400) * 5  # ±5度日变化
        
        # 模拟长期温度趋势
        seasonal_trend = math.sin(time_offset * 2 * math.pi / (86400 * 30)) * 10  # ±10度季节变化
        
        temperature = self._base_temperature + daily_cycle + seasonal_trend
        
        if enable_noise:
            noise = random.gauss(0, 0.5)  # 0.5度标准差的噪声
            temperature += noise
        
        return round(temperature, 2)
    
    def _generate_humidity(self, time_offset: float, enable_noise: bool) -> float:
        """生成湿度数据"""
        # 湿度与温度呈反相关
        daily_cycle = -math.sin(time_offset * 2 * math.pi / 86400) * 15  # ±15%日变化
        
        humidity = self._base_humidity + daily_cycle
        
        if enable_noise:
            noise = random.gauss(0, 2.0)  # 2%标准差的噪声
            humidity += noise
        
        # 限制在合理范围内
        humidity = max(20, min(100, humidity))
        
        return round(humidity, 1)
    
    def _generate_gps(self, time_offset: float, enable_noise: bool) -> Dict[str, float]:
        """生成GPS数据"""
        # 模拟缓慢移动（可能是船只漂移）
        movement_radius = 0.001  # 约100米的活动范围
        movement_speed = 0.0001  # 移动速度
        
        lat_offset = math.sin(time_offset * movement_speed) * movement_radius
        lon_offset = math.cos(time_offset * movement_speed) * movement_radius
        
        latitude = self._base_latitude + lat_offset
        longitude = self._base_longitude + lon_offset
        
        if enable_noise:
            # GPS精度噪声
            lat_noise = random.gauss(0, 0.00001)  # 约1米精度
            lon_noise = random.gauss(0, 0.00001)
            latitude += lat_noise
            longitude += lon_noise
        
        return {
            'latitude': round(latitude, 6),
            'longitude': round(longitude, 6),
            'gps_quality': random.choice(['excellent', 'good', 'fair']) if enable_noise else 'excellent' # type: ignore
        }
    
    def _generate_speed(self, time_offset: float, enable_noise: bool) -> float:
        """生成速度数据"""
        # 模拟船只运动模式
        speed_variation = abs(math.sin(time_offset * 0.01)) * 15  # 0-15节速度变化
        
        speed = self._base_speed + speed_variation
        
        if enable_noise:
            noise = random.gauss(0, 0.5)  # 0.5节噪声
            speed += noise
        
        speed = max(0, speed)  # 速度不能为负
        
        return round(speed, 1)
    
    def _generate_battery_level(self, time_offset: float, enable_noise: bool) -> float:
        """生成电池电量数据"""
        # 电池缓慢消耗
        discharge_rate = 0.1 / 3600  # 每小时消耗0.1%
        discharged = discharge_rate * time_offset
        
        battery_level = max(0, self._battery_level - discharged)
        
        if enable_noise:
            noise = random.gauss(0, 0.5)  # 0.5%噪声
            battery_level += noise
        
        battery_level = max(0, min(100, battery_level))
        
        return round(battery_level, 1)
    
    def _publish_data_events(self, data: Dict[str, Any]):
        """发布数据事件"""
        try:
            # 发布通用数据生成事件
            self.publish_event("sensor.data_generated", data)
            
            # 发布具体类型的数据事件
            if 'temperature' in data:
                self.publish_event("sensor.temperature_update", {
                    'temperature': data['temperature'],
                    'timestamp': data['timestamp']
                })
            
            if 'latitude' in data and 'longitude' in data:
                self.publish_event("sensor.gps_update", {
                    'latitude': data['latitude'],
                    'longitude': data['longitude'],
                    'timestamp': data['timestamp']
                })
            
            # 电池低电量警告
            if 'battery_level' in data and data['battery_level'] < 20:
                self.publish_event("sensor.battery_low", {
                    'battery_level': data['battery_level'],
                    'timestamp': data['timestamp']
                })
            
        except Exception as e:
            self.log_error(f"发布数据事件失败: {e}")
    
    # ==================== 事件处理器 ====================
    
    def _handle_system_startup(self, event_data):
        """处理系统启动事件"""
        try:
            self.log_info("收到系统启动事件")
            if not (self._generation_thread and self._generation_thread.is_alive()):
                self.start_generation()
        except Exception as e:
            self.log_error(f"处理系统启动事件失败: {e}")
    
    def _handle_system_shutdown(self, event_data):
        """处理系统关闭事件"""
        try:
            self.log_info("收到系统关闭事件")
            self.stop_generation()
        except Exception as e:
            self.log_error(f"处理系统关闭事件失败: {e}")
    
    # ==================== 状态管理 ====================
    
    def _save_custom_state(self) -> Dict[str, Any]:
        """保存自定义状态"""
        with self._data_lock:
            return {
                'generation_count': self._generation_count,
                'start_time': self._start_time,
                'battery_level': self._battery_level,
                'latest_data': self._latest_data.copy(),
                'data_history_size': len(self._data_history),
                # 只保存最近50条历史记录到状态
                'recent_history': self._data_history[-50:] if self._data_history else []
            }
    
    def _restore_custom_state(self, custom_state: Dict[str, Any]) -> None:
        """恢复自定义状态"""
        with self._data_lock:
            self._generation_count = custom_state.get('generation_count', 0)
            self._start_time = custom_state.get('start_time', time.time())
            self._battery_level = custom_state.get('battery_level', 100.0)
            self._latest_data = custom_state.get('latest_data', {})
            
            # 恢复历史记录
            recent_history = custom_state.get('recent_history', [])
            self._data_history = recent_history.copy()
        
        self.log_info(f"状态恢复完成，生成次数: {self._generation_count}")
    
    def _wait_for_graceful_shutdown(self) -> None:
        """优雅关闭等待"""
        if self._generation_thread and self._generation_thread.is_alive():
            self.stop_generation()
        
        # 等待线程结束
        if self._generation_thread:
            self._generation_thread.join(timeout=3.0)