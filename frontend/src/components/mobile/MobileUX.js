import React, { useCallback, useLayoutEffect, useRef, useState } from 'react';
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
  const [viewportTop, setViewportTop] = useState(() => (
    typeof window === 'undefined' ? 0 : (window.visualViewport?.offsetTop || 0)
  ));
  const bodyRef = useRef(null);
  const focusTimer = useRef();
  const handleFocusCapture = useCallback((event) => {
    const input = event.target;
    window.clearTimeout(focusTimer.current);
    focusTimer.current = window.setTimeout(() => {
      if (bodyRef.current?.contains(input) && document.activeElement === input) {
        input.scrollIntoView?.({ block: 'nearest', behavior: 'auto' });
      }
    }, 180);
  }, []);

  useLayoutEffect(() => {
    if (!open || typeof window === 'undefined') return undefined;
    const viewport = window.visualViewport;
    let frame;
    const revealInput = () => {
      const activeElement = document.activeElement;
      if (bodyRef.current?.contains(activeElement)
          && /^(INPUT|TEXTAREA|SELECT)$/.test(activeElement.tagName)) {
        activeElement.scrollIntoView?.({ block: 'nearest', behavior: 'auto' });
      }
    };
    const update = () => {
      setViewportHeight(getVisualViewportHeight());
      setViewportTop(viewport?.offsetTop || 0);
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(revealInput);
    };
    update();
    viewport?.addEventListener('resize', update);
    viewport?.addEventListener('scroll', update);
    window.addEventListener('resize', update);
    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(focusTimer.current);
      viewport?.removeEventListener('resize', update);
      viewport?.removeEventListener('scroll', update);
      window.removeEventListener('resize', update);
    };
  }, [open]);

  return { viewportHeight, viewportTop, bodyRef, handleFocusCapture };
};

export const AdaptiveModal = ({
  open,
  rootClassName = '',
  style,
  styles,
  children,
  ...props
}) => {
  const { viewportHeight, viewportTop, bodyRef, handleFocusCapture } = useAdaptiveViewport(open);
  const maxHeight = Math.max(0, viewportHeight - 16);

  return (
    <Modal
      {...props}
      open={open}
      rootClassName={`adaptive-modal ${rootClassName}`.trim()}
      // The height budget and top offset must use the same visible viewport.
      // Ant's default top:100px otherwise pushes a full-height form below it.
      style={{ ...style, top: viewportTop + 8, paddingBottom: 0 }}
      styles={{
        ...styles,
        content: {
          ...styles?.content,
          maxHeight,
        },
      }}
    >
      <div className="adaptive-modal__body" ref={bodyRef} onFocusCapture={handleFocusCapture}>
        {children}
      </div>
    </Modal>
  );
};

export const MobileFilterButton = ({
  activeCount = 0,
  onClick,
  children = '筛选',
  ...buttonProps
}) => (
  <Badge count={activeCount} size="small" offset={[-2, 2]}>
    <Button {...buttonProps} icon={<FilterOutlined />} onClick={onClick}>
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
  const { viewportHeight, bodyRef, handleFocusCapture } = useAdaptiveViewport(open);
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
      <div className="mobile-sheet__body" ref={bodyRef} onFocusCapture={handleFocusCapture}>
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
