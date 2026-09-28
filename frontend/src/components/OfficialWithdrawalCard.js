import React, { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Card, Empty, Spin, Tag, Typography } from 'antd';
import { ArrowRightOutlined, ReloadOutlined, RollbackOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { getOfficialWithdrawalCourses } from '../services/api';
import './OfficialWithdrawalCard.css';

const { Text, Title } = Typography;

const OfficialWithdrawalCard = () => {
  const navigate = useNavigate();
  const [payload, setPayload] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setPayload(await getOfficialWithdrawalCourses());
    } catch (reason) {
      setPayload(null);
      setError(reason?.response?.data?.detail || reason?.message || '官方退课管理读取失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const courses = payload?.courses || [];
  const availableCount = courses.filter(item => item.can_withdraw).length;

  return (
    <Card
      className="official-withdrawal-card"
      title={<div className="official-withdrawal-card__title"><RollbackOutlined /><span>官方退课管理</span></div>}
      extra={<Button type="text" icon={<ReloadOutlined />} loading={loading} onClick={load}>刷新</Button>}
    >
      <div className="official-withdrawal-card__intro">
        <div>
          <Title level={5}>进入退课工作台</Title>
          <Text type="secondary">按教务系统当前退课列表处理。工作台会同时显示课表和培养计划缺口，方便核对退课影响。</Text>
        </div>
        {payload?.term_code && <Tag color="blue">{payload.term_name || payload.term_code}</Tag>}
      </div>
      {error && <Alert showIcon type="warning" message="退课入口暂不可用" description={error} action={<Button size="small" onClick={load}>重试</Button>} />}
      {loading && !payload ? <div className="official-withdrawal-card__loading"><Spin /> 正在读取退课入口…</div> : !payload && error ? null : courses.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={payload?.entry_status === 'closed' ? '教务系统当前未开放退课入口' : '当前没有开放的退课课程'} /> : (
        <div className="official-withdrawal-card__entry">
          <div className="official-withdrawal-card__metrics">
            <div><strong>{courses.length}</strong><span>门官方记录</span></div>
            <div><strong>{availableCount}</strong><span>门当前可退</span></div>
            <div className="official-withdrawal-card__hint">退课入口和选课轮次相互独立，具体课程请进入工作台核对。</div>
          </div>
          <Button type="primary" icon={<ArrowRightOutlined />} onClick={() => navigate('/course-selection/official-withdrawal')}>进入退课工作台</Button>
        </div>
      )}
    </Card>
  );
};

export default OfficialWithdrawalCard;
