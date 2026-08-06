/** Общие элементы интерфейса, повторяющиеся на нескольких экранах. */

import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  InfoCircleOutlined,
  MinusOutlined,
} from '@ant-design/icons';
import { Alert, Card, Empty, Spin, Statistic, Tag, Tooltip, Typography } from 'antd';
import type { CSSProperties, ReactNode } from 'react';

import type { ConfidenceLevel, RunStatus } from '@/api/types';
import {
  CONFIDENCE_COLOR, CONFIDENCE_LABEL, RUN_STATUS_COLOR, RUN_STATUS_LABEL, TRANSPORT_LABEL,
  changeTone, money, signedPercent,
} from '@/utils/format';

const { Text } = Typography;

/** Обязательная оговорка: показатель не является офертой и не средний чек. */
export function MetricDisclaimer({ text }: { text?: string | null }) {
  return (
    <Alert
      type="info"
      showIcon
      icon={<InfoCircleOutlined />}
      message="Расчетная типовая стоимость путешествия"
      description={
        text ??
        'Показатель является расчетной оценкой на основе доступных рыночных предложений. ' +
          'Это не оферта, не средний чек и не фактическая стоимость поездки. Расходы на ' +
          'развлечения, питание вне объекта размещения, трансферы и локальные траты не учитываются.'
      }
      style={{ marginBottom: 16 }}
    />
  );
}

/**
 * Значок справки рядом с названием показателя.
 *
 * Показатели платформы (оценка качества, доверие к источнику) не считываются из
 * названия, а без объяснения читаются как «оценка поездки». Значок ставится
 * справа от подписи везде, где показатель появляется.
 */
export function HelpHint({ text }: { text: ReactNode }) {
  return (
    <Tooltip title={text} overlayStyle={{ maxWidth: 380 }}>
      <InfoCircleOutlined
        style={{ color: '#8c8c8c', marginLeft: 4, cursor: 'help' }}
        onClick={(event) => event.stopPropagation()}
      />
    </Tooltip>
  );
}

/** Подпись показателя вместе со значком справки. */
export function LabelWithHint({ text, hint }: { text: ReactNode; hint: ReactNode }) {
  return (
    <span>
      {text}
      <HelpHint text={hint} />
    </span>
  );
}

export function ConfidenceTag({ level, reason }: { level: ConfidenceLevel | null; reason?: string | null }) {
  if (!level) return <Tag>—</Tag>;
  const tag = <Tag color={CONFIDENCE_COLOR[level]}>{CONFIDENCE_LABEL[level]}</Tag>;
  return reason ? <Tooltip title={reason}>{tag}</Tooltip> : tag;
}

export function RunStatusTag({ status }: { status: RunStatus }) {
  return <Tag color={RUN_STATUS_COLOR[status]}>{RUN_STATUS_LABEL[status] ?? status}</Tag>;
}

/**
 * Направление со способом проезда.
 *
 * Название города не разрывается посередине: перенос допускается только перед
 * плашкой транспорта, и то лишь если места действительно не хватило. Так
 * столбец остается ровным при любой длине названий.
 */
export function RouteCell({
  origin,
  destination,
  transport,
}: {
  origin: string;
  destination: string;
  transport?: string | null;
}) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <span style={{ whiteSpace: 'nowrap' }}>
        <b>{origin}</b> → <b>{destination}</b>
      </span>
      {transport ? <Tag>{TRANSPORT_LABEL[transport] ?? transport}</Tag> : null}
    </span>
  );
}

/**
 * Ценовой диапазон одной строкой.
 *
 * Границы показываются только парой: одна половина диапазона без второй
 * читается как самостоятельная оценка, чем не является.
 */
export function PriceRange({
  low,
  high,
}: {
  low: number | null | undefined;
  high: number | null | undefined;
}) {
  if (low === null || low === undefined || high === null || high === undefined) {
    return <Text type="secondary">—</Text>;
  }
  return (
    <Text type="secondary" style={{ whiteSpace: 'nowrap' }}>
      {money(low)} — {money(high)}
    </Text>
  );
}

export function ChangeIndicator({ value }: { value: number | null | undefined }) {
  const tone = changeTone(value);
  const color = tone === 'up' ? '#cf1322' : tone === 'down' ? '#3f8600' : '#8c8c8c';
  const Icon = tone === 'up' ? ArrowUpOutlined : tone === 'down' ? ArrowDownOutlined : MinusOutlined;
  return (
    <Text style={{ color, whiteSpace: 'nowrap' }}>
      <Icon /> {signedPercent(value)}
    </Text>
  );
}

interface MetricCardProps {
  title: string;
  value: ReactNode;
  suffix?: ReactNode;
  extra?: ReactNode;
  hint?: string;
  loading?: boolean;
  style?: CSSProperties;
}

export function MetricCard({ title, value, suffix, extra, hint, loading, style }: MetricCardProps) {
  // Statistic приводит `value` к строке, поэтому готовый узел (например,
  // индикатор изменения) отдается через formatter: иначе на экран попадает
  // «[object Object]».
  const isNode = value !== null && typeof value === 'object';

  return (
    <Card size="small" loading={loading} style={style}>
      <Statistic
        title={hint ? <LabelWithHint text={title} hint={hint} /> : title}
        value={isNode ? undefined : (value as string | number)}
        formatter={isNode ? () => value : undefined}
        suffix={suffix}
        valueStyle={{ fontSize: 22 }}
      />
      {extra ? <div style={{ marginTop: 8 }}>{extra}</div> : null}
    </Card>
  );
}

interface AsyncBlockProps {
  loading: boolean;
  error: string | null;
  empty?: boolean;
  emptyText?: string;
  children: ReactNode;
  minHeight?: number;
}

/** Единая обработка состояний загрузки, ошибки и пустого результата. */
export function AsyncBlock({
  loading, error, empty, emptyText, children, minHeight = 120,
}: AsyncBlockProps) {
  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight }}>
        <Spin />
      </div>
    );
  }
  if (error) {
    return <Alert type="error" showIcon message="Не удалось загрузить данные" description={error} />;
  }
  if (empty) {
    return <Empty description={emptyText ?? 'Нет данных'} image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  }
  return <>{children}</>;
}

export function PageTitle({ title, subtitle, extra }: { title: string; subtitle?: string; extra?: ReactNode }) {
  return (
    <div
      style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start',
        marginBottom: 16, gap: 16, flexWrap: 'wrap',
      }}
    >
      <div>
        <Typography.Title level={3} style={{ margin: 0 }}>
          {title}
        </Typography.Title>
        {subtitle ? <Text type="secondary">{subtitle}</Text> : null}
      </div>
      {extra ? <div>{extra}</div> : null}
    </div>
  );
}
