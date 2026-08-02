import React, { useCallback, useEffect, useState } from 'react';
import { Badge, Button, Drawer, Grid, Modal, Space, Tag } from 'antd';
import { FilterOutlined } from '@ant-design/icons';
import './MobileUX.css';

export const useIsMobile = () => {
  const screens = Grid.useBreakpoint();
  return !screens.md;
};

const getVisualViewportHeight = () => {
  if (typeof window === 'undefined') return 720;
  return Math.round(window.visualViewport?.height || window.innerHeight || 720);
};

const useAdaptiveViewport = (open) => {
  const [viewportHeight, setViewportHeight] = useState(getVisualViewportHeight);

  useEffect(() => {
    if (!open || typeof window === 'undefined') return undefined;
    const viewport = window.visualViewport;
    const update = () => {
      setViewportHeight(getVisualViewportHeight());
      window.requestAnimationFrame(() => {
        const activeElement = document.activeElement;
        if (
          activeElement
          && /^(INPUT|TEXTAREA|SELECT)$/.test(activeElement.tagName)
        ) {
          activeElement.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }
      });
    };

    update();
    viewport?.addEventListener('resize', update);
    viewport?.addEventListener('scroll', update);
    window.addEventListener('resize', update);
    return () => {
      viewport?.removeEventListener('resize', update);
      viewport?.removeEventListener('scroll', update);
      window.removeEventListener('resize', update);
    };
  }, [open]);

  const handleFocusCapture = useCallback((event) => {
    window.setTimeout(() => {
      event.target?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
    }, 180);
  }, []);

  return { viewportHeight, handleFocusCapture };
};

export const AdaptiveModal = ({
  open,
  rootClassName = '',
  styles,
  children,
  ...props
}) => {
  const { viewportHeight, handleFocusCapture } = useAdaptiveViewport(open);
  const maxHeight = Math.max(96, viewportHeight - 16);

  return (
    <Modal
      {...props}
      open={open}
      rootClassName={`adaptive-modal ${rootClassName}`.trim()}
      styles={{
        ...styles,
        content: {
          ...styles?.content,
          maxHeight,
        },
      }}
    >
      <div className="adaptive-modal__body" onFocusCapture={handleFocusCapture}>
        {children}
      </div>
    </Modal>
  );
};

export const MobileFilterButton = ({
  activeCount = 0,
  onClick,
  children = '筛选',
}) => (
  <Badge count={activeCount} size="small" offset={[-2, 2]}>
    <Button icon={<FilterOutlined />} onClick={onClick}>
      {children}
    </Button>
  </Badge>
);

export const MobileFilterChips = ({ items = [], onClear }) => {
  if (!items.length) return null;
  return (
    <div className="mobile-filter-chips" aria-label="当前筛选条件">
      {items.map((item) => (
        <Tag
          key={item.key}
          closable={Boolean(onClear)}
          onClose={() => onClear?.(item.key)}
        >
          {item.label}
        </Tag>
      ))}
    </div>
  );
};

export const MobileFilterDrawer = ({
  open,
  onClose,
  onApply,
  onReset,
  title = '筛选与排序',
  children,
}) => {
  const { viewportHeight, handleFocusCapture } = useAdaptiveViewport(open);
  const maxHeight = Math.max(96, Math.min(720, viewportHeight - 8));

  return (
    <Drawer
      rootClassName="mobile-sheet-root"
      className="mobile-sheet"
      placement="bottom"
      height="auto"
      title={title}
      open={open}
      onClose={onClose}
      destroyOnClose={false}
      styles={{
        wrapper: { maxHeight },
        content: { maxHeight },
      }}
      footer={(
        <div className="mobile-sheet__footer">
          <Button onClick={onReset}>重置</Button>
          <Button type="primary" onClick={onApply}>应用</Button>
        </div>
      )}
    >
      <div className="mobile-sheet__body" onFocusCapture={handleFocusCapture}>
        {children}
      </div>
    </Drawer>
  );
};

export const MobileDetailDrawer = ({
  open,
  onClose,
  title,
  children,
  footer,
  width = '100%',
}) => (
  <Drawer
    className="mobile-detail-drawer"
    placement="right"
    width={width}
    title={title}
    open={open}
    onClose={onClose}
    footer={footer}
  >
    {children}
  </Drawer>
);

export const MobileActionBar = ({ children, visible = true, className = '' }) => {
  if (!visible) return null;
  return (
    <div
      className={`mobile-action-bar ${className}`.trim()}
      role="group"
      aria-label="页面操作"
    >
      <Space.Compact block>{children}</Space.Compact>
    </div>
  );
};
