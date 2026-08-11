import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, Breadcrumb, Button, Card, Empty, Modal, Skeleton, Space, Tag, Typography, message,
} from 'antd';
import {
  DownloadOutlined, FileDoneOutlined, FileTextOutlined, ReloadOutlined, SafetyCertificateOutlined,
} from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { generateAcademicDocument, getAcademicDocuments } from '../services/api';
import './AcademicDocumentsPage.css';

const { Title, Text, Paragraph } = Typography;

const categoryIcon = category => (category.includes('成绩')
  ? <FileTextOutlined /> : <SafetyCertificateOutlined />);

const saveOrOpen = ({ blob, filename, format }, previewWindow = null) => {
  const url = URL.createObjectURL(blob);
  if (format === 'html') {
    const opened = previewWindow || window.open(url, '_blank');
    if (opened) {
      if (previewWindow) previewWindow.location.replace(url);
      window.setTimeout(() => URL.revokeObjectURL(url), 60000);
      return;
    }
  }
  if (previewWindow && !previewWindow.closed) previewWindow.close();
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
};

const AcademicDocumentsPage = () => {
  const [groups, setGroups] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [generatingId, setGeneratingId] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError('');
    try {
      const payload = await getAcademicDocuments();
      setGroups(Array.isArray(payload?.groups) ? payload.groups : []);
    } catch (error) {
      setLoadError(error.response?.data?.detail || error.message || '无法读取可打印证明');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const documentCount = useMemo(() => groups.reduce(
    (total, group) => total + (group.documents?.length || 0), 0,
  ), [groups]);

  const generate = useCallback((document) => {
    Modal.confirm({
      title: `生成${document.name}`,
      icon: <FileDoneOutlined />,
      content: (
        <div className="academic-documents-confirm">
          <Paragraph>教务系统将实时生成这份证明，成功生成可能计入本学期打印次数。</Paragraph>
          {document.semester_limit && (
            <Text type="secondary">官方每学期打印上限：{document.semester_limit} 次</Text>
          )}
        </div>
      ),
      okText: '确认生成',
      cancelText: '取消',
      async onOk() {
        const previewWindow = window.open('', '_blank');
        if (previewWindow) {
          previewWindow.document.title = `正在生成${document.name}`;
          previewWindow.document.body.textContent = '正在从教务系统生成证明，请稍候…';
        }
        setGeneratingId(document.id);
        try {
          const result = await generateAcademicDocument(document.id);
          saveOrOpen(result, previewWindow);
          message.success(`${document.name}已生成`);
        } catch (error) {
          if (previewWindow && !previewWindow.closed) previewWindow.close();
          message.error(error.response?.data?.detail || error.message || '证明生成失败');
          throw error;
        } finally {
          setGeneratingId('');
        }
      },
    });
  }, []);

  return (
    <main className="academic-documents-page">
      <Breadcrumb items={[
        { title: <Link to="/export">导出下载</Link> },
        { title: '学籍证明与成绩单' },
      ]} />
      <header className="academic-documents-page__header">
        <div>
          <Title level={3}><SafetyCertificateOutlined /> 学籍证明与成绩单</Title>
          <Text type="secondary">直接使用教务系统当前提供的证明类型，实时生成，不在本地保存副本。</Text>
        </div>
        <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新可用证明</Button>
      </header>

      <Alert
        type="warning"
        showIcon
        message="生成前请确认所需语言和证明类型"
        description="每种证明均有学期打印次数上限。只有点击“生成证明”并确认后才会请求文件；页面不会预取，也不会自动批量生成。若已达上限，请联系学院教学办。"
      />

      {loading && (
        <section className="academic-documents-loading" aria-label="正在读取可打印证明">
          <Skeleton active paragraph={{ rows: 5 }} />
        </section>
      )}
      {!loading && loadError && (
        <Alert
          type="error"
          showIcon
          message="无法读取可打印证明"
          description={loadError}
          action={<Button onClick={load}>重试</Button>}
        />
      )}
      {!loading && !loadError && documentCount === 0 && (
        <Empty description="当前账号暂无可打印证明" />
      )}
      {!loading && !loadError && groups.map(group => (
        <section className="academic-document-group" key={group.name}>
          <div className="academic-document-group__heading">
            <span className="academic-document-group__icon">{categoryIcon(group.name)}</span>
            <div>
              <Title level={4}>{group.name}</Title>
              <Text type="secondary">{group.documents?.length || 0} 项可用</Text>
            </div>
          </div>
          <div className="academic-document-grid">
            {(group.documents || []).map(document => (
              <Card key={document.id} className="academic-document-card">
                <div className="academic-document-card__body">
                  <div>
                    <Title level={5}>{document.name}</Title>
                    <Space size={6} wrap>
                      <Tag color="blue">官方实时生成</Tag>
                      {document.semester_limit && <Tag>每学期最多 {document.semester_limit} 次</Tag>}
                    </Space>
                  </div>
                  <Button
                    type="primary"
                    icon={<DownloadOutlined />}
                    loading={generatingId === document.id}
                    disabled={Boolean(generatingId) && generatingId !== document.id}
                    onClick={() => generate(document)}
                  >生成证明</Button>
                </div>
              </Card>
            ))}
          </div>
        </section>
      ))}
    </main>
  );
};

export default AcademicDocumentsPage;
