/**
 * Выгрузка текущей выборки прямо со страницы со списком.
 *
 * Отдельная вкладка «Экспорт» требовала заново задавать фильтры, уже заданные
 * в таблице. Здесь кнопка ставит задачу, дожидается ее завершения и скачивает
 * файл — пользователю не нужно знать, что выгрузка выполняется фоном.
 */

import { DownloadOutlined } from '@ant-design/icons';
import { App, Button } from 'antd';
import { useEffect, useRef, useState } from 'react';

import { ApiError, api, downloadFile } from '@/api/client';

const TERMINAL = ['SUCCESS', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'PARTIAL'];
const POLL_MS = 1500;
/** Ограничение на ожидание, чтобы кнопка не осталась «в работе» навсегда. */
const MAX_ATTEMPTS = 60;

type QueryValue = string | number | boolean | null | undefined;

interface ExportButtonProps {
  /** Набор данных API: SCENARIO_RUNS, OFFERS, SOURCE_METRICS. */
  dataset?: string;
  /** Фильтры выборки — те же, что применены к таблице на экране. */
  filters?: Record<string, QueryValue>;
  label?: string;
  format?: 'CSV' | 'XLSX';
}

export function ExportButton({
  dataset = 'SCENARIO_RUNS',
  filters = {},
  label = 'Выгрузить',
  format = 'CSV',
}: ExportButtonProps) {
  const { message } = App.useApp();
  const [busy, setBusy] = useState(false);
  const cancelled = useRef(false);

  useEffect(() => () => {
    cancelled.current = true;
  }, []);

  const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

  const run = async () => {
    setBusy(true);
    const hide = message.loading('Готовим выгрузку…', 0);
    try {
      const created = await api.createExport({ dataset, format }, filters);
      const jobId = String(created.job_id);

      for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt += 1) {
        if (cancelled.current) return;
        const job = await api.exportJob(jobId);
        if (TERMINAL.includes(job.status)) {
          if (job.status === 'FAILED' || job.status === 'TIMED_OUT') {
            throw new ApiError('EXPORT_FAILED', job.error_message ?? 'Выгрузка не удалась', 500);
          }
          await downloadFile(api.downloadExportUrl(jobId), `export.${format.toLowerCase()}`);
          message.success('Файл выгружен');
          return;
        }
        await wait(POLL_MS);
      }

      // Задача не потеряна — она осталась в очереди и доступна администратору.
      message.warning('Выгрузка выполняется дольше обычного, файл появится в разделе «Экспорт»');
    } catch (exc) {
      if (cancelled.current) return;
      message.error(exc instanceof ApiError ? exc.message : 'Не удалось выгрузить данные');
    } finally {
      hide();
      if (!cancelled.current) setBusy(false);
    }
  };

  return (
    <Button icon={<DownloadOutlined />} loading={busy} onClick={run}>
      {label}
    </Button>
  );
}
