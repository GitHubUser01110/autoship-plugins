"""
数据分析插件 - plugin.py

分析传感器数据，计算统计信息、检测异常和生成报告
依赖数据生成插件提供数据源
"""

import time
import statistics
import threading
from collections import deque, defaultdict
from typing import Dict, List, Any, Optional, Union
from datetime import datetime, timezone

from plugins.foundation.plugin_base import Plugin, Response


class DataAnalyzerPlugin(Plugin):
    """数据分析器插件"""
    
    VERSION = '1.0.0'
    MIN_COMPATIBLE_VERSION = '1.0.0'
    
    def __init__(self, plugin_id: str, plugin_base):
        super().__init__(plugin_id, plugin_base)
        
        # 数据存储
        self._data_lock = threading.Lock()
        self._sensor_data = deque(maxlen=1000)  # 最近1000条数据
        self._statistics_cache = {}
        self._alerts = []
        self._anomalies = []
        
        # 分析状态
        self._last_analysis_time = 0
        self._analysis_count = 0
        
        # 趋势分析数据
        self._trend_data = defaultdict(list)
        
        self.logger.info("数据分析插件初始化完成")
    
    # ==================== 生命周期方法 ====================
    
    def _handle_enable(self) -> Response:
        """插件启用时调用"""
        try:
            # 检查依赖插件
            dependency_check = self._check_data_generator_dependency()
            if not dependency_check.success:
                return dependency_check
            
            # 订阅数据生成器的事件
            self.subscribe_event("sensor.data_generated", self._handle_sensor_data)
            self.subscribe_event("sensor.temperature_update", self._handle_temperature_update)
            self.subscribe_event("sensor.gps_update", self._handle_gps_update)
            self.subscribe_event("sensor.battery_low", self._handle_battery_low)
            
            # 获取历史数据
            self._load_historical_data()
            
            self.log_info("数据分析插件已启用")
            return Response(success=True, data="数据分析插件启用成功")
            
        except Exception as e:
            self.log_error(f"启用插件失败: {e}")
            return Response(success=False, data=str(e))
    
    def _handle_disable(self) -> Response:
        """插件禁用时调用"""
        try:
            # 清理临时数据
            self.clear_transient_state()
            
            self.log_info("数据分析插件已禁用")
            return Response(success=True, data="数据分析插件禁用成功")
            
        except Exception as e:
            self.log_error(f"禁用插件失败: {e}")
            return Response(success=False, data=str(e))
    
    def _check_data_generator_dependency(self) -> Response:
        """检查数据生成器依赖"""
        try:
            # 尝试调用数据生成器的接口
            result = self.plugin_base.call_plugin_interface(
                "data_generator", 
                "get_latest_data"
            )
            
            if result.success:
                self.log_info("数据生成器依赖检查通过")
                return Response(success=True)
            else:
                return Response(
                    success=False,
                    data="数据生成器插件未启用或无法访问"
                )
        except Exception as e:
            return Response(
                success=False,
                data=f"依赖检查失败: {e}"
            )
    
    # ==================== 插件接口方法 ====================
    
    def get_analysis_report(self, input_data: Any = None) -> Dict[str, Any]:
        """获取分析报告"""
        try:
            window_seconds = 60  # 默认1分钟窗口
            if isinstance(input_data, dict):
                window_seconds = input_data.get('window_seconds', 60)
            elif isinstance(input_data, (int, float)):
                window_seconds = input_data
            
            current_time = time.time()
            window_data = []
            
            with self._data_lock:
                for data in reversed(self._sensor_data):
                    if current_time - data.get('timestamp', 0) <= window_seconds:
                        window_data.append(data)
                    else:
                        break
            
            if not window_data:
                return {
                    'error': '没有可分析的数据',
                    'data_count': 0,
                    'window_seconds': window_seconds
                }
            
            # 生成分析报告
            report = self._generate_analysis_report(window_data, window_seconds) # type: ignore
            
            # 发布报告生成事件
            self.publish_event("analysis.report_generated", {
                'report_summary': {
                    'data_count': len(window_data),
                    'window_seconds': window_seconds,
                    'alert_count': len(report.get('alerts', [])),
                    'anomaly_count': len(report.get('anomalies', []))
                },
                'timestamp': current_time
            })
            
            return report
            
        except Exception as e:
            self.log_error(f"生成分析报告失败: {e}")
            return {'error': str(e)}
    
    def get_statistics(self, input_data: Any = None) -> Dict[str, Any]:
        """获取统计信息"""
        try:
            data_type = None
            if isinstance(input_data, dict):
                data_type = input_data.get('data_type')
            elif isinstance(input_data, str):
                data_type = input_data
            
            with self._data_lock:
                if data_type:
                    # 返回特定类型的统计
                    return self._statistics_cache.get(data_type, {})
                else:
                    # 返回所有统计信息
                    return self._statistics_cache.copy()
                    
        except Exception as e:
            self.log_error(f"获取统计信息失败: {e}")
            return {'error': str(e)}
    
    def get_alerts(self, input_data: Any = None) -> List[Dict[str, Any]]:
        """获取告警信息"""
        try:
            limit = 50  # 默认返回最近50条告警
            if isinstance(input_data, dict):
                limit = input_data.get('limit', 50)
            elif isinstance(input_data, int):
                limit = input_data
            
            with self._data_lock:
                return self._alerts[-limit:] if self._alerts else []
                
        except Exception as e:
            self.log_error(f"获取告警信息失败: {e}")
            return []
    
    def analyze_data(self, input_data: Any = None) -> Dict[str, Any]:
        """执行数据分析"""
        try:
            # 强制执行一次分析
            self._perform_analysis()
            
            return {
                'analysis_completed': True,
                'analysis_count': self._analysis_count,
                'last_analysis_time': self._last_analysis_time,
                'data_points_analyzed': len(self._sensor_data)
            }
            
        except Exception as e:
            self.log_error(f"执行数据分析失败: {e}")
            return {'error': str(e)}
    
    # ==================== 数据分析核心逻辑 ====================
    
    def _generate_analysis_report(self, data: List[Dict[str, Any]], window_seconds: int) -> Dict[str, Any]:
        """生成分析报告"""
        report = {
            'generated_at': time.time(),
            'window_seconds': window_seconds,
            'data_count': len(data),
            'statistics': {},
            'trends': {},
            'alerts': [],
            'anomalies': [],
            'summary': {}
        }
        
        if not data:
            return report
        
        # 计算统计信息
        report['statistics'] = self._calculate_statistics(data)
        
        # 趋势分析
        if self.get_config('enable_trending', True):
            report['trends'] = self._calculate_trends(data)
        
        # 检测异常和告警
        alerts, anomalies = self._detect_alerts_and_anomalies(data)
        report['alerts'] = alerts
        report['anomalies'] = anomalies
        
        # 生成摘要
        report['summary'] = self._generate_summary(report)
        
        return report
    
    def _calculate_statistics(self, data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """计算统计信息"""
        stats = {}
        methods = self.get_config('statistical_methods', ['mean', 'std', 'min', 'max'])
        
        # 按数据类型分组
        data_by_type = defaultdict(list)
        for item in data:
            for key, value in item.items():
                if isinstance(value, (int, float)) and key != 'timestamp':
                    data_by_type[key].append(value)
        
        # 计算各种统计指标
        for data_type, values in data_by_type.items():
            if not values:
                continue
                
            type_stats = {}
            
            if 'mean' in methods:
                type_stats['mean'] = round(statistics.mean(values), 3)
            
            if 'median' in methods:
                type_stats['median'] = round(statistics.median(values), 3)
            
            if 'std' in methods and len(values) > 1:
                type_stats['std'] = round(statistics.stdev(values), 3)
            
            if 'min' in methods:
                type_stats['min'] = min(values)
            
            if 'max' in methods:
                type_stats['max'] = max(values)
            
            if 'percentile' in methods and len(values) >= 4:
                sorted_values = sorted(values)
                n = len(sorted_values)
                type_stats['percentile_25'] = sorted_values[n // 4]
                type_stats['percentile_75'] = sorted_values[3 * n // 4]
            
            type_stats['count'] = len(values)
            stats[data_type] = type_stats
        
        return stats
    
    def _calculate_trends(self, data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """计算趋势信息"""
        trends = {}
        
        # 按数据类型分组并按时间排序
        data_by_type = defaultdict(list)
        for item in data:
            timestamp = item.get('timestamp', time.time())
            for key, value in item.items():
                if isinstance(value, (int, float)) and key != 'timestamp':
                    data_by_type[key].append((timestamp, value))
        
        # 计算趋势
        for data_type, time_values in data_by_type.items():
            if len(time_values) < 3:  # 需要至少3个点计算趋势
                continue
            
            # 按时间排序
            time_values.sort(key=lambda x: x[0])
            
            # 简单线性趋势计算
            values = [v for _, v in time_values]
            n = len(values)
            
            # 计算斜率（简单的线性回归）
            if n >= 2:
                x_mean = (n - 1) / 2  # 时间索引的平均值
                y_mean = sum(values) / n
                
                numerator = sum((i - x_mean) * (values[i] - y_mean) for i in range(n))
                denominator = sum((i - x_mean) ** 2 for i in range(n))
                
                if denominator != 0:
                    slope = numerator / denominator
                    
                    if abs(slope) < 0.001:
                        trend_direction = 'stable'
                    elif slope > 0:
                        trend_direction = 'increasing'
                    else:
                        trend_direction = 'decreasing'
                    
                    trends[data_type] = {
                        'direction': trend_direction,
                        'slope': round(slope, 6),
                        'confidence': 'medium'  # 简化的置信度
                    }
        
        return trends
    
    def _detect_alerts_and_anomalies(self, data: List[Dict[str, Any]]) -> tuple:
        """检测告警和异常"""
        alerts = []
        anomalies = []
        thresholds = self.get_config('alert_thresholds', {})
        
        for item in data:
            timestamp = item.get('timestamp', time.time())
            
            # 检查阈值告警
            for key, value in item.items():
                if not isinstance(value, (int, float)):
                    continue
                
                # 温度告警
                if key == 'temperature':
                    high_threshold = thresholds.get('temperature_high', 35.0)
                    low_threshold = thresholds.get('temperature_low', 0.0)
                    
                    if value > high_threshold:
                        alerts.append({
                            'type': 'temperature_high',
                            'value': value,
                            'threshold': high_threshold,
                            'timestamp': timestamp,
                            'severity': 'high'
                        })
                    elif value < low_threshold:
                        alerts.append({
                            'type': 'temperature_low',
                            'value': value,
                            'threshold': low_threshold,
                            'timestamp': timestamp,
                            'severity': 'medium'
                        })
                
                # 湿度告警
                elif key == 'humidity':
                    high_threshold = thresholds.get('humidity_high', 90.0)
                    low_threshold = thresholds.get('humidity_low', 30.0)
                    
                    if value > high_threshold:
                        alerts.append({
                            'type': 'humidity_high',
                            'value': value,
                            'threshold': high_threshold,
                            'timestamp': timestamp,
                            'severity': 'medium'
                        })
                    elif value < low_threshold:
                        alerts.append({
                            'type': 'humidity_low',
                            'value': value,
                            'threshold': low_threshold,
                            'timestamp': timestamp,
                            'severity': 'low'
                        })
                
                # 电池告警
                elif key == 'battery_level':
                    low_threshold = thresholds.get('battery_low', 15.0)
                    
                    if value < low_threshold:
                        alerts.append({
                            'type': 'battery_low',
                            'value': value,
                            'threshold': low_threshold,
                            'timestamp': timestamp,
                            'severity': 'critical'
                        })
        
        # 简单的异常检测（基于统计方法）
        anomalies = self._detect_statistical_anomalies(data)
        
        return alerts, anomalies
    
    def _detect_statistical_anomalies(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """基于统计方法检测异常"""
        anomalies = []
        
        if len(data) < 10:  # 数据太少无法检测异常
            return anomalies
        
        # 按数据类型分组
        data_by_type = defaultdict(list)
        for item in data:
            timestamp = item.get('timestamp', time.time())
            for key, value in item.items():
                if isinstance(value, (int, float)) and key != 'timestamp':
                    data_by_type[key].append((timestamp, value))
        
        # 对每种数据类型检测异常
        for data_type, time_values in data_by_type.items():
            if len(time_values) < 10:
                continue
            
            values = [v for _, v in time_values]
            
            # 计算均值和标准差
            mean_val = statistics.mean(values)
            if len(values) > 1:
                std_val = statistics.stdev(values)
                
                # 3-sigma规则检测异常
                threshold = 3 * std_val
                
                for timestamp, value in time_values:
                    if abs(value - mean_val) > threshold:
                        anomalies.append({
                            'type': f'{data_type}_anomaly',
                            'value': value,
                            'expected_range': [mean_val - threshold, mean_val + threshold],
                            'deviation': abs(value - mean_val),
                            'timestamp': timestamp,
                            'detection_method': '3_sigma'
                        })
        
        return anomalies
    
    def _generate_summary(self, report: Dict[str, Any]) -> Dict[str, Any]:
        """生成报告摘要"""
        summary = {
            'data_quality': 'good',
            'alert_level': 'normal',
            'key_insights': [],
            'recommendations': []
        }
        
        # 评估数据质量
        data_count = report.get('data_count', 0)
        if data_count < 5:
            summary['data_quality'] = 'poor'
            summary['key_insights'].append('数据点数量不足，建议增加采样频率')
        elif data_count < 20:
            summary['data_quality'] = 'fair'
        
        # 评估告警级别
        alerts = report.get('alerts', [])
        critical_alerts = [a for a in alerts if a.get('severity') == 'critical']
        high_alerts = [a for a in alerts if a.get('severity') == 'high']
        
        if critical_alerts:
            summary['alert_level'] = 'critical'
            summary['key_insights'].append(f'发现 {len(critical_alerts)} 个严重告警')
        elif high_alerts:
            summary['alert_level'] = 'high'
            summary['key_insights'].append(f'发现 {len(high_alerts)} 个高级告警')
        elif alerts:
            summary['alert_level'] = 'medium'
        
        # 趋势洞察
        trends = report.get('trends', {})
        for data_type, trend_info in trends.items():
            direction = trend_info.get('direction')
            if direction == 'increasing':
                summary['key_insights'].append(f'{data_type} 呈上升趋势')
            elif direction == 'decreasing':
                summary['key_insights'].append(f'{data_type} 呈下降趋势')
        
        # 异常洞察
        anomalies = report.get('anomalies', [])
        if anomalies:
            summary['key_insights'].append(f'检测到 {len(anomalies)} 个数据异常')
            summary['recommendations'].append('建议检查传感器状态和环境因素')
        
        # 基本建议
        if not summary['recommendations']:
            summary['recommendations'].append('系统运行正常，建议继续监控')
        
        return summary
    
    def _perform_analysis(self):
        """执行分析"""
        try:
            current_time = time.time()
            analysis_window = self.get_config('analysis_window', 60)
            
            # 获取窗口内的数据
            window_data = []
            with self._data_lock:
                for data in reversed(self._sensor_data):
                    if current_time - data.get('timestamp', 0) <= analysis_window:
                        window_data.append(data)
                    else:
                        break
            
            if window_data:
                # 更新统计缓存
                self._statistics_cache = self._calculate_statistics(window_data)
                
                # 检测新的告警和异常
                new_alerts, new_anomalies = self._detect_alerts_and_anomalies(window_data)
                
                # 添加新告警到历史记录
                with self._data_lock:
                    self._alerts.extend(new_alerts)
                    self._anomalies.extend(new_anomalies)
                    
                    # 限制历史记录大小
                    if len(self._alerts) > 1000:
                        self._alerts = self._alerts[-1000:]
                    if len(self._anomalies) > 500:
                        self._anomalies = self._anomalies[-500:]
                
                # 发布统计更新事件
                if self._statistics_cache:
                    self.publish_event("analysis.statistics_updated", {
                        'statistics_summary': {k: v.get('mean', 0) for k, v in self._statistics_cache.items()},
                        'timestamp': current_time
                    })
                
                # 发布告警事件
                for alert in new_alerts:
                    self.publish_event("analysis.alert_triggered", alert)
                
                # 发布异常事件
                for anomaly in new_anomalies:
                    self.publish_event("analysis.anomaly_detected", anomaly)
            
            self._last_analysis_time = current_time
            self._analysis_count += 1
            
        except Exception as e:
            self.log_error(f"执行分析失败: {e}")
    
    # ==================== 事件处理器 ====================
    
    def _handle_sensor_data(self, event_data):
        """处理传感器数据事件"""
        try:
            data = event_data.data
            if data and isinstance(data, dict):
                with self._data_lock:
                    self._sensor_data.append(data)
                
                # 定期执行分析
                current_time = time.time()
                if current_time - self._last_analysis_time > 30:  # 每30秒分析一次
                    self._perform_analysis()
        
        except Exception as e:
            self.log_error(f"处理传感器数据事件失败: {e}")
    
    def _handle_temperature_update(self, event_data):
        """处理温度更新事件"""
        try:
            data = event_data.data
            temperature = data.get('temperature')
            
            if temperature is not None:
                # 温度相关的特殊处理
                thresholds = self.get_config('alert_thresholds', {})
                high_threshold = thresholds.get('temperature_high', 35.0)
                
                if temperature > high_threshold:
                    self.log_warning(f"温度过高告警: {temperature}°C")
        
        except Exception as e:
            self.log_error(f"处理温度更新事件失败: {e}")
    
    def _handle_gps_update(self, event_data):
        """处理GPS更新事件"""
        try:
            data = event_data.data
            lat = data.get('latitude')
            lon = data.get('longitude')
            
            if lat is not None and lon is not None:
                # GPS相关的特殊处理
                self.set_transient_state('last_gps_position', {'lat': lat, 'lon': lon})
        
        except Exception as e:
            self.log_error(f"处理GPS更新事件失败: {e}")
    
    def _handle_battery_low(self, event_data):
        """处理电池低电量事件"""
        try:
            data = event_data.data
            battery_level = data.get('battery_level')
            
            if battery_level is not None:
                # 电池低电量的特殊处理
                alert = {
                    'type': 'battery_critical',
                    'value': battery_level,
                    'timestamp': time.time(),
                    'severity': 'critical',
                    'message': f'电池电量严重不足: {battery_level}%'
                }
                
                with self._data_lock:
                    self._alerts.append(alert)
                
                self.publish_event("analysis.alert_triggered", alert)
                self.log_warning(f"电池低电量告警: {battery_level}%")
        
        except Exception as e:
            self.log_error(f"处理电池低电量事件失败: {e}")
    
    # ==================== 数据加载 ====================
    
    def _load_historical_data(self):
        """加载历史数据"""
        try:
            # 从数据生成器获取历史数据
            result = self.plugin_base.call_plugin_interface(
                "data_generator",
                "get_data_history",
                {"limit": 100}
            )
            
            if result.success and result.data:
                historical_data = result.data
                
                with self._data_lock:
                    # 清空现有数据
                    self._sensor_data.clear()
                    
                    # 添加历史数据
                    for data in historical_data:
                        self._sensor_data.append(data)
                
                self.log_info(f"加载了 {len(historical_data)} 条历史数据")
                
                # 执行初始分析
                self._perform_analysis()
            else:
                self.log_warning("无法获取历史数据")
        
        except Exception as e:
            self.log_error(f"加载历史数据失败: {e}")
    
    # ==================== 状态管理 ====================
    
    def _save_custom_state(self) -> Dict[str, Any]:
        """保存自定义状态"""
        with self._data_lock:
            return {
                'analysis_count': self._analysis_count,
                'last_analysis_time': self._last_analysis_time,
                'statistics_cache': self._statistics_cache.copy(),
                'recent_alerts': self._alerts[-50:] if self._alerts else [],
                'recent_anomalies': self._anomalies[-20:] if self._anomalies else [],
                'sensor_data_count': len(self._sensor_data)
            }
    
    def _restore_custom_state(self, custom_state: Dict[str, Any]) -> None:
        """恢复自定义状态"""
        with self._data_lock:
            self._analysis_count = custom_state.get('analysis_count', 0)
            self._last_analysis_time = custom_state.get('last_analysis_time', 0)
            self._statistics_cache = custom_state.get('statistics_cache', {})
            self._alerts = custom_state.get('recent_alerts', [])
            self._anomalies = custom_state.get('recent_anomalies', [])
        
        self.log_info(f"状态恢复完成，分析次数: {self._analysis_count}")