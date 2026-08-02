import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert, Breadcrumb, Button, Card, Col, DatePicker, Descriptions, Drawer, Empty,
  Form, Grid, Input, Modal, Radio, Row, Segmented, Select, Space, Spin, Statistic, Tag, Tooltip,
  Typography, message,
} from 'antd';
import {
  DownloadOutlined, EyeOutlined, ReloadOutlined, SearchOutlined, TrophyOutlined,
} from '@ant-design/icons';
import { Link } from 'react-router-dom';
import dayjs from 'dayjs';
import { useCachedResource } from '../resources/ResourceStore';
import {
  deleteFestivalActivitiesCache, downloadFestivalCertificates, getFestivalActivities,
} from '../services/api';
import {
  academicYearChoices, activityHasAward, activityHasCertificate,
  activityInRange, activityStart, currentAcademicYear,
} from '../export/festivalActivityUtils';
import {
  AUTOMATIC_MODE, ON_DEMAND_MODE, festivalDataModeFromStorageEvent, persistFestivalDataMode,
  preferredFestivalDataMode,
} from '../export/festivalDataMode';
import {
  MobileFilterButton, MobileFilterChips, MobileFilterDrawer,
} from '../components/mobile/MobileUX';
import './FestivalActivitiesPage.css';

const { RangePicker } = DatePicker;
const { Paragraph, Text, Title } = Typography;

const pick = (activity, ...keys) => keys.map(key => activity?.[key]).find(
  value => value !== undefined && value !== null && value !== '',
);

const activityKey = (activity, index) => pick(
  activity, 'key', 'id', 'record_id', 'activity_id',
) || `${pick(activity, 'festival', 'festival_label', 'section') || 'festival'}-${index}`;

const normalizeActivities = (payload) => payload?.activities || payload?.items || [];
const warningText = (warning) => (
  typeof warning === 'string'
    ? warning
    : warning?.message || warning?.detail || '部分活动详情不完整'
);
const durationText = (activity) => {
  const duration = pick(activity, 'duration', 'duration_hours');
  if (duration === undefined || duration === null || duration === '') return '—';
  return typeof duration === 'number' ? `${duration} 小时` : duration;
};

const FestivalActivitiesPage = ({ offlineMode = false }) => {
  const screens = Grid.useBreakpoint();
  const isMobile = !screens.md;
  const defaultAcademicYear = useMemo(() => currentAcademicYear(), []);
  const initialMode = preferredFestivalDataMode(localStorage, { offlineMode });
  const [configurationConfirmed, setConfigurationConfirmed] = useState(false);
  const [dataMode, setDataMode] = useState(null);
  const [draftMode, setDraftMode] = useState(initialMode);
  const [draftRange, setDraftRange] = useState([
    defaultAcademicYear.start, defaultAcademicYear.end,
  ]);
  const [draftAcademicYearKey, setDraftAcademicYearKey] = useState(defaultAcademicYear.key);
  const [directData, setDirectData] = useState(null);
  const [directLoading, setDirectLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [confirmingConfiguration, setConfirmingConfiguration] = useState(false);
  const [directError, setDirectError] = useState('');
  const [range, setRange] = useState([defaultAcademicYear.start, defaultAcademicYear.end]);
  const [semesterKey, setSemesterKey] = useState(defaultAcademicYear.key);
  const [customRange, setCustomRange] = useState(false);
  const [view, setView] = useState('range');
  const [festival, setFestival] = useState('all');
  const [keyword, setKeyword] = useState('');
  const [detail, setDetail] = useState(null);
  const [downloading, setDownloading] = useState(false);
  const [mobileFilterOpen, setMobileFilterOpen] = useState(false);
  const [mobileDraft, setMobileDraft] = useState({
    academicYearKey: defaultAcademicYear.key,
    range: [defaultAcademicYear.start, defaultAcademicYear.end],
    festival: 'all',
    keyword: '',
    view: 'range',
  });
  const resource = useCachedResource('festival-activities', {
    enabled: configurationConfirmed && dataMode === AUTOMATIC_MODE,
  });
  const data = dataMode === AUTOMATIC_MODE ? resource.data : directData;
  const activities = normalizeActivities(data);
  const loading = dataMode === AUTOMATIC_MODE ? resource.loading : directLoading;
  const error = dataMode === AUTOMATIC_MODE
    ? (resource.error?.response?.data?.detail || resource.error?.message || resource.syncError || '')
    : directError;

  useEffect(() => {
    const onStorage = (event) => {
      if (festivalDataModeFromStorageEvent(event) !== ON_DEMAND_MODE) return;
      resource.clear();
      if (offlineMode) {
        setDataMode(null);
        setDraftMode(AUTOMATIC_MODE);
        setConfigurationConfirmed(false);
        setDirectLoading(false);
        message.warning('其他窗口已关闭自动保存并清除本地数据；当前离线，无法继续读取四节活动');
        return;
      }
      setDraftMode(ON_DEMAND_MODE);
      setDataMode(ON_DEMAND_MODE);
      setDirectData(null);
      setDirectError('');
      if (configurationConfirmed) setDirectLoading(true);
      message.info('其他窗口已切换为每次使用按需读取，本页已停止使用已保存数据');
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, [configurationConfirmed, offlineMode, resource.clear]);

  const semesters = useMemo(() => {
    return academicYearChoices(activities);
  }, [activities]);
  const academicYearSelectOptions = useMemo(() => semesters.map(item => ({
    value: item.key,
    label: item.key === defaultAcademicYear.key ? `${item.label}（当前）` : item.label,
  })), [defaultAcademicYear.key, semesters]);

  useEffect(() => {
    if (!configurationConfirmed || dataMode !== ON_DEMAND_MODE || offlineMode) return undefined;
    let active = true;
    setDirectLoading(true);
    setDirectError('');
    getFestivalActivities()
      .then((payload) => {
        if (active) setDirectData(payload);
      })
      .catch((requestError) => {
        if (active) {
          setDirectError(requestError.response?.data?.detail || requestError.message || '活动数据读取失败');
        }
      })
      .finally(() => {
        if (active) setDirectLoading(false);
      });
    return () => { active = false; };
  }, [configurationConfirmed, dataMode, offlineMode]);

  const festivals = useMemo(() => [...new Set(activities.map(activity => (
    pick(activity, 'festival_label', 'festival', 'section')
  )).filter(Boolean))], [activities]);

  const visibleActivities = useMemo(() => {
    const query = keyword.trim().toLocaleLowerCase();
    return activities.filter((activity) => {
      const start = activityStart(activity);
      const hasTime = dayjs(start).isValid();
      if (view === 'range' && !activityInRange(activity, range)) return false;
      if (view === 'unknown' && hasTime) return false;
      if (view === 'certificate' && !activityHasCertificate(activity)) return false;
      if (view === 'award' && !activityHasAward(activity)) return false;
      if (festival !== 'all'
        && pick(activity, 'festival_label', 'festival', 'section') !== festival) return false;
      if (query && ![
        pick(activity, 'title', 'name'), activity.team_name, activity.department,
      ].some(value => String(value || '').toLocaleLowerCase().includes(query))) return false;
      return true;
    });
  }, [activities, festival, keyword, range, view]);

  const rangeActivities = useMemo(
    () => activities.filter(activity => activityInRange(activity, range)),
    [activities, range],
  );
  const certificateCount = rangeActivities.filter(activityHasCertificate).length;
  const unknownCount = activities.filter(activity => !dayjs(activityStart(activity)).isValid()).length;
  const warnings = data?.warnings || [];
  const savedAt = dataMode === AUTOMATIC_MODE ? resource.metadata?.savedAt : null;
  const resourceSyncing = dataMode === AUTOMATIC_MODE
    && ['starting', 'queued', 'running'].includes(resource.syncState);
  const initialLoading = !data && (loading || refreshing || resourceSyncing);
  const refreshingWithData = Boolean(data) && (refreshing || resourceSyncing);
  const viewLabels = {
    range: '日期范围内',
    all: '全部活动',
    certificate: '有证书',
    award: '有奖',
    unknown: '时间未知',
  };
  const mobileFilterChips = [
    range?.[0] && range?.[1] && {
      key: 'range',
      label: `${range[0].format('YYYY-MM-DD')} 至 ${range[1].format('YYYY-MM-DD')}`,
    },
    festival !== 'all' && { key: 'festival', label: festival },
    keyword && { key: 'keyword', label: `搜索：${keyword}` },
    view !== 'range' && { key: 'view', label: viewLabels[view] },
  ].filter(Boolean);
  const mobileActiveFilterCount = [
    customRange,
    festival !== 'all',
    Boolean(keyword),
    view !== 'range',
  ].filter(Boolean).length;

  const selectSemester = (key) => {
    const selected = semesters.find(item => item.key === key);
    if (!selected) return;
    setSemesterKey(key);
    setRange([selected.start, selected.end]);
    setCustomRange(false);
    setView('range');
  };

  const changeRange = (value) => {
    setRange(value);
    setCustomRange(true);
    setSemesterKey('custom');
    setView('range');
  };

  const openMobileFilters = () => {
    setMobileDraft({
      academicYearKey: semesterKey,
      range,
      festival,
      keyword,
      view,
    });
    setMobileFilterOpen(true);
  };

  const selectMobileAcademicYear = (key) => {
    const selected = semesters.find(item => item.key === key);
    if (!selected) return;
    setMobileDraft(current => ({
      ...current,
      academicYearKey: key,
      range: [selected.start, selected.end],
    }));
  };

  const applyMobileFilters = () => {
    setSemesterKey(mobileDraft.academicYearKey);
    setRange(mobileDraft.range);
    setCustomRange(mobileDraft.academicYearKey === 'custom');
    setFestival(mobileDraft.festival);
    setKeyword(mobileDraft.keyword);
    setView(mobileDraft.view);
    setMobileFilterOpen(false);
  };

  const resetMobileFilters = () => {
    setMobileDraft({
      academicYearKey: defaultAcademicYear.key,
      range: [defaultAcademicYear.start, defaultAcademicYear.end],
      festival: 'all',
      keyword: '',
      view: 'range',
    });
  };

  const selectDraftAcademicYear = (key) => {
    const selected = semesters.find(item => item.key === key);
    if (!selected) return;
    setDraftAcademicYearKey(key);
    setDraftRange([selected.start, selected.end]);
  };

  const changeDraftRange = (value) => {
    setDraftRange(value);
    setDraftAcademicYearKey('custom');
  };

  const confirmConfiguration = async () => {
    if (!draftRange?.[0] || !draftRange?.[1]) {
      message.warning('请选择完整的开始和结束日期');
      return;
    }
    if (offlineMode && draftMode !== AUTOMATIC_MODE) {
      message.warning('离线状态只能读取已保存的数据');
      return;
    }
    setConfirmingConfiguration(true);
    try {
      if (draftMode === ON_DEMAND_MODE) {
        await deleteFestivalActivitiesCache();
        resource.clear();
        setDirectData(null);
        setDirectLoading(true);
        message.success('已关闭自动保存，并清除四节活动本地数据');
      }
      setRange(draftRange);
      setSemesterKey(draftAcademicYearKey);
      setCustomRange(draftAcademicYearKey === 'custom');
      setDataMode(draftMode);
      setConfigurationConfirmed(true);
      persistFestivalDataMode(localStorage, draftMode, { offlineMode });
    } catch (requestError) {
      message.error(requestError.response?.data?.detail || '无法清除已保存的四节活动数据，读取方式未更改');
    } finally {
      setConfirmingConfiguration(false);
    }
  };

  const refreshNow = async () => {
    if (offlineMode) return false;
    setRefreshing(true);
    try {
      if (dataMode === AUTOMATIC_MODE) {
        await resource.refresh();
        await resource.reloadAndApply();
      } else {
        setDirectError('');
        setDirectData(await getFestivalActivities());
      }
      message.success(dataMode === AUTOMATIC_MODE ? '四节活动已刷新并保存' : '四节活动已按需重新读取');
      return true;
    } catch (requestError) {
      const detail = requestError.response?.data?.detail || requestError.message || '重新获取四节活动失败';
      if (dataMode === ON_DEMAND_MODE) setDirectError(detail);
      message.error(detail);
      return false;
    } finally {
      setRefreshing(false);
    }
  };

  const applyLatestSnapshot = () => {
    resource.applyAvailable();
    message.success('已应用最新四节活动数据');
  };

  const downloadArchive = async () => {
    if (!range?.[0] || !range?.[1] || downloading || offlineMode) return;
    if (range[1].diff(range[0], 'day') > 369) {
      message.warning('日期范围最长为 370 天');
      return;
    }
    setDownloading(true);
    try {
      const result = await downloadFestivalCertificates({
        startDate: range[0].format('YYYY-MM-DD'),
        endDate: range[1].format('YYYY-MM-DD'),
      });
      const url = URL.createObjectURL(result.blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = result.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      if (result.failed > 0) {
        message.warning(`已下载 ${result.succeeded} 张证书，${result.failed} 张失败；详情见压缩包内下载说明`);
      } else {
        message.success(result.succeeded > 0
          ? `已打包下载 ${result.succeeded} 张证书`
          : '证书压缩包已开始下载');
      }
    } catch (error) {
      message.error(error.response?.data?.detail || error.message || '证书打包失败');
    } finally {
      setDownloading(false);
    }
  };

  const renderCard = (activity, index) => {
    const start = activityStart(activity);
    const title = pick(activity, 'title', 'name') || '未命名活动';
    return (
      <Card className="festival-card" key={activityKey(activity, index)}>
        <div className="festival-card__heading">
          <div>
            <Space size={[6, 6]} wrap>
              <Tag color="blue">{pick(activity, 'festival_label', 'festival', 'section') || '四节活动'}</Tag>
              {activity.status && <Tag>{activity.status}</Tag>}
              {activityHasCertificate(activity) && <Tag color="green">有证书</Tag>}
            </Space>
            <Title level={5}>{title}</Title>
          </div>
        </div>
        <dl className="festival-card__summary">
          <div><dt>活动时间</dt><dd>{activity.activity_time || start || '时间未知'}</dd></div>
          <div><dt>活动地点</dt><dd>{activity.location || '未公布'}</dd></div>
          <div><dt>活动类别</dt><dd>{pick(activity, 'category', 'activity_type', 'type') || '未分类'}</dd></div>
          {activity.award && <div><dt>获奖情况</dt><dd>{activity.award}</dd></div>}
        </dl>
        {activity.detail_warning && (
          <Text className="festival-card__warning" type="warning">{activity.detail_warning}</Text>
        )}
        <Button icon={<EyeOutlined />} onClick={() => setDetail(activity)}>查看详情</Button>
      </Card>
    );
  };

  return (
    <main className="festival-page">
      <Modal
        title="选择四节活动时间与读取方式"
        open={!configurationConfirmed}
        closable={false}
        maskClosable={false}
        keyboard={false}
        destroyOnHidden={false}
        footer={(
          <Button type="primary" onClick={confirmConfiguration} loading={confirmingConfiguration} block={isMobile}>
            确认并读取活动
          </Button>
        )}
      >
        <Paragraph type="secondary">
          先选择要查看和导出证书的日期范围。活动时间需要进入每条详情确认，因此确认后会读取完整参加记录，再按日期筛选。
        </Paragraph>
        <div className="festival-entry-form">
          <label>
            <span>学年快捷选择</span>
            <Select
              value={draftAcademicYearKey}
              onChange={selectDraftAcademicYear}
              options={[
                ...academicYearSelectOptions,
                ...(draftAcademicYearKey === 'custom'
                  ? [{ value: 'custom', label: '自定义范围', disabled: true }]
                  : []),
              ]}
            />
          </label>
          <label>
            <span>开始和结束日期（含首尾）</span>
            <RangePicker format="YYYY-MM-DD" value={draftRange} onChange={changeDraftRange} allowClear={false} />
          </label>
          <fieldset>
            <legend>数据读取方式</legend>
            <Radio.Group
              value={draftMode}
              onChange={event => setDraftMode(event.target.value)}
              optionType="button"
              buttonStyle="solid"
              options={[
                { label: '自动读取并保存', value: AUTOMATIC_MODE },
                { label: '每次使用按需读取', value: ON_DEMAND_MODE, disabled: offlineMode },
              ]}
            />
          </fieldset>
          <Alert
            type={draftMode === AUTOMATIC_MODE ? 'info' : 'warning'}
            showIcon
            message={draftMode === AUTOMATIC_MODE ? '自动读取并保存' : '每次使用按需读取'}
            description={draftMode === AUTOMATIC_MODE
              ? '优先显示当前账号已保存的数据，并在后台检查更新；新数据会保存到本机，可供离线查看。'
              : '每次进入或手动重新读取时连接学校系统，本次活动数据不会写入本地缓存。'}
          />
          {offlineMode && (
            <Text type="secondary">离线状态无法从学校系统读取，只能使用自动模式下已有的本地数据。</Text>
          )}
        </div>
      </Modal>
      <Breadcrumb items={[
        { title: <Link to="/export">导出下载</Link> },
        { title: '四节活动' },
      ]} />
      <header className="festival-page__header">
        <div>
          <Title level={3}><TrophyOutlined /> 四节活动</Title>
          <Text type="secondary">查看创意节、科普节、科技节和创业节参加记录，并批量导出证书。</Text>
          {configurationConfirmed && (
            <Space size={8} wrap className="festival-page__mode">
              <Tag color={dataMode === AUTOMATIC_MODE ? 'blue' : 'orange'}>
                {dataMode === AUTOMATIC_MODE ? '自动读取并保存' : '每次使用按需读取'}
              </Tag>
              {savedAt && <Text type="secondary">本地数据更新于 {new Date(savedAt).toLocaleString()}</Text>}
            </Space>
          )}
        </div>
        <Space wrap>
          <Button onClick={() => setConfigurationConfirmed(false)}>时间与读取方式</Button>
          <Tooltip title={offlineMode ? '离线模式不会连接活动系统' : ''}>
            <Button
              icon={<ReloadOutlined />}
              disabled={offlineMode}
              loading={refreshing}
              onClick={() => refreshNow()}
            >{dataMode === AUTOMATIC_MODE ? '刷新并保存' : '重新读取'}</Button>
          </Tooltip>
        </Space>
      </header>

      {offlineMode && <Alert type="info" showIcon message="当前显示已保存的本地数据" description="活动筛选和详情可继续查看；刷新和证书导出需要连接学校系统，当前已停用。" />}
      {dataMode === AUTOMATIC_MODE && resource.updateAvailable && (
        <Alert
          type="success"
          showIcon
          message="四节活动已有更新"
          description="后台检查已完成。应用后会保留当前日期与筛选条件，并更新活动列表和证书数量。"
          action={<Button size="small" type="primary" onClick={applyLatestSnapshot}>应用最新数据</Button>}
        />
      )}
      {error && <Alert type="error" showIcon message="四节活动获取失败" description={error} />}
      {warnings.length > 0 && <Alert type="warning" showIcon message={`有 ${warnings.length} 项数据需要留意`} description={warnings.slice(0, 3).map(warningText).join('；')} />}

      {initialLoading && (
        <div className="festival-loading" role="status" aria-live="polite">
          <Spin size="large" />
          <Text>正在汇总四个分区的活动详情</Text>
        </div>
      )}

      {!initialLoading && isMobile && (
        <section className="festival-mobile-controls" aria-label="四节活动筛选与导出">
          <div className="festival-mobile-controls__actions">
            <MobileFilterButton
              activeCount={mobileActiveFilterCount}
              onClick={openMobileFilters}
            >
              筛选与查看范围
            </MobileFilterButton>
            <Tooltip title={offlineMode
              ? '离线模式不能下载证书'
              : certificateCount === 0 ? '当前数据未发现证书，导出时仍会实时检查' : '导出时会实时检查学校系统中的最新证书'}>
              <Button
                type="primary"
                icon={<DownloadOutlined />}
                loading={downloading}
                disabled={offlineMode || !range}
                onClick={downloadArchive}
              >
                打包证书（{certificateCount}）
              </Button>
            </Tooltip>
          </div>
          <MobileFilterChips items={mobileFilterChips} />
        </section>
      )}

      {!initialLoading && !isMobile && <Card className="festival-controls">
        <div className="festival-controls__grid">
          <label><span>学年快捷选择</span><Select value={semesterKey || undefined} placeholder="选择学年" onChange={selectSemester} options={[
            ...academicYearSelectOptions,
            ...(customRange ? [{ value: 'custom', label: '自定义范围', disabled: true }] : []),
          ]} /></label>
          <label><span>日期范围（含首尾）</span><RangePicker format="YYYY-MM-DD" value={range} onChange={changeRange} allowClear={false} /></label>
          <label><span>活动分区</span><Select value={festival} onChange={setFestival} options={[
            { value: 'all', label: '全部分区' },
            ...festivals.map(value => ({ value, label: value })),
          ]} /></label>
          <label><span>搜索活动</span><Input allowClear prefix={<SearchOutlined />} value={keyword} onChange={event => setKeyword(event.target.value)} placeholder="名称、队伍或部门" /></label>
        </div>
        <div className="festival-controls__footer">
          <Segmented value={view} onChange={setView} options={[
            { value: 'range', label: '日期范围内' }, { value: 'all', label: '全部' },
            { value: 'certificate', label: '有证书' },
            { value: 'award', label: '有奖' },
            { value: 'unknown', label: `时间未知 ${unknownCount}` },
          ]} />
          <Tooltip title={offlineMode
            ? '离线模式不能下载证书'
            : certificateCount === 0 ? '当前数据未发现证书，导出时仍会实时检查' : '导出时会实时检查学校系统中的最新证书'}>
            <Button type="primary" icon={<DownloadOutlined />} loading={downloading} disabled={offlineMode || !range} onClick={downloadArchive}>
              打包下载证书（当前数据 {certificateCount}）
            </Button>
          </Tooltip>
        </div>
      </Card>}

      {!initialLoading && <Row gutter={[12, 12]} className="festival-stats">
        <Col xs={8}><Card><Statistic title="范围内活动" value={rangeActivities.length} /></Card></Col>
        <Col xs={8}><Card><Statistic title="可下载证书" value={certificateCount} /></Card></Col>
        <Col xs={8}><Card><Statistic title="时间未知" value={unknownCount} /></Card></Col>
      </Row>}

      {!initialLoading && refreshingWithData && (
        <Alert
          className="festival-refreshing-alert"
          type="info"
          showIcon
          message="正在后台刷新四节活动"
          description="当前活动和筛选仍可继续使用，刷新完成后会提示应用最新数据。"
        />
      )}

      {!initialLoading && (visibleActivities.length ? (
          <section className="festival-grid" aria-label="活动列表">
            {visibleActivities.map(renderCard)}
          </section>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={error || '当前条件下没有活动'} />
      ))}

      {isMobile && (
        <MobileFilterDrawer
          open={mobileFilterOpen}
          onClose={() => setMobileFilterOpen(false)}
          onApply={applyMobileFilters}
          onReset={resetMobileFilters}
          title="筛选四节活动"
        >
          <Form layout="vertical">
            <Form.Item label="学年快捷选择">
              <Select
                value={mobileDraft.academicYearKey}
                onChange={selectMobileAcademicYear}
                options={[
                  ...academicYearSelectOptions,
                  ...(mobileDraft.academicYearKey === 'custom'
                    ? [{ value: 'custom', label: '自定义范围', disabled: true }]
                    : []),
                ]}
              />
            </Form.Item>
            <Form.Item label="日期范围（含首尾）">
              <RangePicker
                format="YYYY-MM-DD"
                value={mobileDraft.range}
                onChange={value => setMobileDraft(current => ({
                  ...current,
                  academicYearKey: 'custom',
                  range: value,
                }))}
                allowClear={false}
              />
            </Form.Item>
            <Form.Item label="活动分区">
              <Select
                value={mobileDraft.festival}
                onChange={value => setMobileDraft(current => ({ ...current, festival: value }))}
                options={[
                  { value: 'all', label: '全部分区' },
                  ...festivals.map(value => ({ value, label: value })),
                ]}
              />
            </Form.Item>
            <Form.Item label="搜索活动">
              <Input
                allowClear
                prefix={<SearchOutlined />}
                value={mobileDraft.keyword}
                onChange={event => setMobileDraft(current => ({
                  ...current,
                  keyword: event.target.value,
                }))}
                placeholder="名称、队伍或部门"
              />
            </Form.Item>
            <Form.Item label="查看范围">
              <Select
                value={mobileDraft.view}
                onChange={value => setMobileDraft(current => ({ ...current, view: value }))}
                options={[
                  { value: 'range', label: '日期范围内' },
                  { value: 'all', label: '全部活动' },
                  { value: 'certificate', label: '有证书' },
                  { value: 'award', label: '有奖' },
                  { value: 'unknown', label: `时间未知 ${unknownCount}` },
                ]}
              />
            </Form.Item>
          </Form>
        </MobileFilterDrawer>
      )}

      <Drawer title="活动详情" width={isMobile ? '100%' : 680} open={Boolean(detail)} onClose={() => setDetail(null)}>
        {detail && <>
          <Title level={4}>{pick(detail, 'title', 'name') || '未命名活动'}</Title>
          <Space wrap className="festival-detail__tags">
            <Tag color="blue">{pick(detail, 'festival_label', 'festival', 'section') || '四节活动'}</Tag>
            {detail.status && <Tag>{detail.status}</Tag>}
            {activityHasCertificate(detail) && <Tag color="green">已提供证书</Tag>}
          </Space>
          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="活动时间">{detail.activity_time || activityStart(detail) || '时间未知'}</Descriptions.Item>
            <Descriptions.Item label="活动时长">{durationText(detail)}</Descriptions.Item>
            <Descriptions.Item label="活动地点">{detail.location || '—'}</Descriptions.Item>
            <Descriptions.Item label="组织部门">{detail.department || '—'}</Descriptions.Item>
            <Descriptions.Item label="活动类别">{pick(detail, 'category', 'activity_type', 'type') || '—'}</Descriptions.Item>
            <Descriptions.Item label="队伍名称">{detail.team_name || '—'}</Descriptions.Item>
            <Descriptions.Item label="获奖情况">{detail.award || '—'}</Descriptions.Item>
            <Descriptions.Item label="签到 / 签退">{[pick(detail, 'sign_in', 'checkin_status'), pick(detail, 'sign_out', 'checkout_status')].filter(Boolean).join(' / ') || '—'}</Descriptions.Item>
            <Descriptions.Item label="报名时间">{detail.registration_time || [detail.registration_start, detail.registration_end].filter(Boolean).join(' 至 ') || '—'}</Descriptions.Item>
          </Descriptions>
          <Title level={5}>活动简介</Title>
          <Paragraph className="festival-detail__text">{detail.description || '暂无简介'}</Paragraph>
          <Title level={5}>注意事项</Title>
          <Paragraph className="festival-detail__text">{detail.notes || '暂无额外注意事项'}</Paragraph>
        </>}
      </Drawer>
    </main>
  );
};

export default FestivalActivitiesPage;
