const logger = require('./logger');

class TimerManager {
  constructor() {
    // 存储格式：clientId-channel -> { timerId, delayId, remaining, message, targetWs, channel, sourceWs }
    // 延迟覆盖也必须在 Map 中登记；clear/断线不能留下尚未启动的孤儿任务。
    this.timers = new Map();
  }
  
  /**
   * 发送消息队列
   * @param {string} clientId 
   * @param {string} channel 
   * @param {WebSocket} targetWs - 目标 WebSocket 连接
   * @param {object} message 
   * @param {number} totalSends - 总发送次数
   * @param {number} timeSpace - 发送间隔（毫秒）
   * @param {WebSocket} sourceWs - 消息来源 WebSocket（用于通知）
   */
  sendMessage(clientId, channel, targetWs, message, totalSends, timeSpace, sourceWs) {
    const timerKey = `${clientId}-${channel}`;
    
    // 检查是否已有该通道的定时器在运行
    if (this.timers.has(timerKey)) {
      // 有正在运行的定时器，需要先清除（覆盖旧消息）
      logger.info(`[${timerKey}] 清除现有定时器，准备发送新消息`);
      
      const oldTask = this.timers.get(timerKey);
      this.clearTimer(clientId, channel, oldTask.sourceWs);
      
      // 发送清除 APP 队列指令
      const clearMessage = {
        type: 'msg',
        clientId: clientId,
        targetId: oldTask.message.targetId,
        message: `clear-${channel === 'A' ? '1' : '2'}`
      };
      
      try {
        targetWs.send(JSON.stringify(clearMessage));
      } catch (err) {
        logger.error(`发送清除指令失败：${err.message}`);
      }
      
      // 延迟 150ms 再发送新消息，避免队列指令晚于波形数据执行。
      // 占位对象即本次任务 token；新消息/clear 替换它后，迟到回调无权再发送。
      const pendingTask = {
        clientId, channel, targetWs, sourceWs,
        message: JSON.parse(JSON.stringify(message)),
        remaining: totalSends, timerId: null, delayId: null,
        startedAt: new Date()
      };
      this.timers.set(timerKey, pendingTask);
      pendingTask.delayId = setTimeout(() => {
        if (this.timers.get(timerKey) !== pendingTask) {
          return;
        }
        this._startSending(clientId, channel, targetWs, message, totalSends, timeSpace, sourceWs);
      }, 150);
      
      // 通知客户端正在覆盖
      if (sourceWs && sourceWs.readyState === 1) {
        try {
          sourceWs.send(JSON.stringify({
            type: 'notify',
            clientId,
            targetId: '',
            message: `当前通道${channel}有正在发送的消息，覆盖之前的消息`
          }));
        } catch (err) {
          logger.error(`发送覆盖通知失败：${err.message}`);
        }
      }
      
      return;
    }
    
    // 不存在未发完的消息，直接开始发送
    this._startSending(clientId, channel, targetWs, message, totalSends, timeSpace, sourceWs);
  }
  
  /**
   * 内部方法：启动发送消息
   * @private
   */
  _startSending(clientId, channel, targetWs, message, totalSends, timeSpace, sourceWs) {
    const timerKey = `${clientId}-${channel}`;
    
    // 创建定时器任务
    const timerTask = {
      clientId,
      channel,
      targetWs,
      sourceWs,
      message: JSON.parse(JSON.stringify(message)), // 深拷贝
      remaining: totalSends,
      timeSpace,
      timerId: null,
      delayId: null,
      startedAt: new Date()
    };
    this.timers.set(timerKey, timerTask);
    
    // 立即发送第一条消息
    this._safeSend(targetWs, message);
    timerTask.remaining--;
    
    logger.info(`[${timerKey}] 消息发送中，剩余次数：${timerTask.remaining}`);
    
    // 如果还有剩余次数，启动定时器
    if (timerTask.remaining > 0) {
      const timerId = setInterval(() => {
        // clearInterval 不能撤回已经入队的回调，必须再验证任务归属。
        if (this.timers.get(timerKey) !== timerTask) {
          clearInterval(timerId);
          return;
        }
        // 检查目标连接是否还存在
        if (targetWs.readyState !== 1) { // 1 = OPEN
          logger.warn(`[${timerKey}] 目标连接已断开，停止发送`);
          clearInterval(timerId);
          this.timers.delete(timerKey);
          return;
        }
        
        this._safeSend(targetWs, message);
        timerTask.remaining--;
        
        if (timerTask.remaining <= 0) {
          logger.info(`[${timerKey}] 消息发送完毕`);
          clearInterval(timerId);
          this.timers.delete(timerKey);
          
          // 通知客户端发送完毕（保持 JSON 格式）
          if (sourceWs && sourceWs.readyState === 1) {
            try {
              sourceWs.send(JSON.stringify({
                type: 'notify',
                clientId,
                targetId: timerTask.message.targetId,
                message: '发送完毕'
              }));
            } catch (err) {
              logger.error(`发送完成通知失败：${err.message}`);
            }
          }
        }
      }, timeSpace);
      
      timerTask.timerId = timerId;
      this.timers.set(timerKey, timerTask);
    } else {
      this.timers.delete(timerKey);
      logger.info(`[${timerKey}] 消息已发送完成（仅 1 条）`);
      
      // 通知客户端发送完毕
      if (sourceWs && sourceWs.readyState === 1) {
        try {
          sourceWs.send(JSON.stringify({
            type: 'notify',
            clientId,
            targetId: timerTask.message.targetId,
            message: '发送完毕'
          }));
        } catch (err) {
          logger.error(`发送完成通知失败：${err.message}`);
        }
      }
    }
  }
  
  /**
   * 清除指定通道的所有定时器
   * @param {string} clientId 
   * @param {string} channel 
   * @param {WebSocket} sourceWs - 来源 WebSocket（用于通知）
   */
  clearTimer(clientId, channel, sourceWs) {
    const timerKey = `${clientId}-${channel}`;
    const timerTask = this.timers.get(timerKey);
    
    if (timerTask) {
      // 先作废任务，再发清除消息；即使旧回调已入队也不能恢复发送。
      this.timers.delete(timerKey);
      if (timerTask.delayId !== null && timerTask.delayId !== undefined) {
        clearTimeout(timerTask.delayId);
      }
      // 先发送清除指令
      const clearMessage = {
        type: 'msg',
        clientId: clientId,
        targetId: timerTask.message.targetId,
        message: `clear-${channel === 'A' ? '1' : '2'}`
      };
      this._safeSend(timerTask.targetWs, clearMessage);
      
      // 清除定时器
      if (timerTask.timerId) {
        clearInterval(timerTask.timerId);
      }
      
      logger.debug(`[${timerKey}] 定时器已清除`);
    }
  }
  
  /**
   * 清除指定客户端的所有定时器
   * @param {string} clientId 
   */
  clearClientTimers(clientId) {
    const keysToDelete = [];
    
    for (const [key, task] of this.timers.entries()) {
      if (task.clientId === clientId || task.message.targetId === clientId) {
        keysToDelete.push(key);
      }
    }
    
    keysToDelete.forEach(key => {
      const task = this.timers.get(key);
      if (task) {
        this.clearTimer(task.clientId, task.channel, task.sourceWs);
        this._zeroTask(task);
      }
    });
    
    logger.info(`[${clientId}] 清除了 ${keysToDelete.length} 个定时器`);
  }
  
  /**
   * 安全发送消息
   * @private
   * @param {WebSocket} ws 
   * @param {object} message 
   */
  _safeSend(ws, message) {
    if (ws && ws.readyState === 1) { // 1 = OPEN
      try {
        ws.send(JSON.stringify(message));
      } catch (err) {
        logger.error(`发送消息失败：${err.message}`);
      }
    }
  }

  /** 断线/退出时尽力归零；普通覆盖 clear 不归零，以免抵消新动作的强度。 */
  _zeroTask(task) {
    this._safeSend(task.targetWs, {
      type: 'msg', clientId: task.clientId, targetId: task.message.targetId,
      message: `strength-${task.channel === 'A' ? '1' : '2'}+2+0`
    });
  }
  
  /**
   * 获取定时器统计
   * @returns {object}
   */
  getStats() {
    return {
      activeTimers: this.timers.size,
      timers: Array.from(this.timers.entries()).map(([key, task]) => ({
        key,
        clientId: task.clientId,
        channel: task.channel,
        remaining: task.remaining,
        startedAt: task.startedAt
      }))
    };
  }
  
  /**
   * 清理所有定时器（用于服务关闭）
   */
  cleanupAll() {
    for (const task of Array.from(this.timers.values())) {
      this.clearTimer(task.clientId, task.channel, task.sourceWs);
      this._zeroTask(task);
    }
    this.timers.clear();
    logger.info('所有定时器已清理');
  }
}

module.exports = new TimerManager();
