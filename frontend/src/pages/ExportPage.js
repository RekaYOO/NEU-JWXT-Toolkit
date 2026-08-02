import React from 'react';
import { Card, Tag, Typography } from 'antd';
import { ArrowRightOutlined, CloudServerOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { exportTools, getExportToolAvailability } from '../export/exportTools';
import './ExportPage.css';

const { Title, Text, Paragraph } = Typography;

const ExportPage = ({ offlineMode = false, offlineCapabilities = {} }) => {
  const navigate = useNavigate();

  return (
    <main className="export-page">
      <header className="export-page__header">
        <div>
          <Text className="export-page__eyebrow">EXPORT WORKSPACE</Text>
          <Title level={2}>导出下载</Title>
          <Paragraph type="secondary">
            集中查看和整理校内系统中的个人数据。新的导出工具会持续加入这里。
          </Paragraph>
        </div>
        {offlineMode && <Tag icon={<CloudServerOutlined />}>只读离线模式</Tag>}
      </header>

      <section className="export-tool-grid" aria-label="导出工具">
        {exportTools.map((tool) => {
          const availability = getExportToolAvailability(tool, {
            offlineMode, offlineCapabilities,
          });
          const Icon = tool.icon;
          return (
            <Card
              key={tool.id}
              className={`export-tool-card${availability.available ? '' : ' is-disabled'}`}
              hoverable={availability.available}
              role="button"
              tabIndex={availability.available ? 0 : -1}
              aria-disabled={!availability.available}
              onClick={() => availability.available && navigate(tool.path)}
              onKeyDown={(event) => {
                if (availability.available && (event.key === 'Enter' || event.key === ' ')) {
                  event.preventDefault();
                  navigate(tool.path);
                }
              }}
            >
              <div className="export-tool-card__icon"><Icon /></div>
              <div className="export-tool-card__content">
                <div className="export-tool-card__title">
                  <Title level={4}>{tool.title}</Title>
                  {availability.available && <ArrowRightOutlined aria-hidden="true" />}
                </div>
                <Paragraph>{tool.description}</Paragraph>
                {!availability.available && (
                  <Text type="secondary">{availability.reason}</Text>
                )}
              </div>
            </Card>
          );
        })}
      </section>
    </main>
  );
};

export default ExportPage;
