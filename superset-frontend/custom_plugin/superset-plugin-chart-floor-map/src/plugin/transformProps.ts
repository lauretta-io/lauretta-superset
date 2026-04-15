/**
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */
import { ChartProps, TimeseriesDataRecord } from '@superset-ui/core';

/**
 * Dashboard filter columns that can be applied to Retail layer in polygon mode.
 * In heatmap mode, all data is used without client-side filters.
 */
const UNIT_FILTER_COLUMNS = new Set(['unit_name', 'unit_group_name']);
const INCLUSIVE_OPERATORS = new Set(['IN', '=', '==', 'EQ', 'IS']);

type UnitFilterValues = Record<string, Set<string>>;

interface DashboardFilter {
  col?: string;
  column?: string;
  subject?: string;
  val?: unknown | unknown[];
}

interface FloorMapFormData {
  hot_threshold?: unknown;
  hotThreshold?: unknown;
  adhoc_filters?: AdhocFilter[];
  adhocFilters?: AdhocFilter[];
  extra_form_data?: {
    filters?: DashboardFilter[];
    hot_threshold?: unknown;
    hotThreshold?: unknown;
  };
  extraFormData?: {
    filters?: DashboardFilter[];
    hot_threshold?: unknown;
    hotThreshold?: unknown;
  };
}

interface AdhocFilter {
  clause?: string;
  isExtra?: boolean;
  expressionType?: string;
  subject?: string;
  operator?: string;
  operatorId?: string;
  comparator?: unknown | unknown[];
}

function toStringArray(value: unknown | unknown[]): string[] {
  const items = Array.isArray(value) ? value : [value];
  return items
    .flatMap(v => {
      if (v === null || v === undefined) return [];
      if (typeof v === 'object') {
        const maybeObj = v as Record<string, unknown>;
        if (maybeObj.value !== undefined && maybeObj.value !== null) {
          return [String(maybeObj.value)];
        }
        if (maybeObj.label !== undefined && maybeObj.label !== null) {
          return [String(maybeObj.label)];
        }
      }
      return [String(v)];
    })
    .map(s => s.trim())
    .filter(Boolean);
}

function addFilterValues(
  result: UnitFilterValues,
  rawCol: string,
  rawValues: unknown | unknown[],
) {
  const col = String(rawCol || '').trim();
  if (!UNIT_FILTER_COLUMNS.has(col)) {
    return;
  }

  const values = toStringArray(rawValues);
  if (!values.length) return;

  if (!result[col]) result[col] = new Set<string>();
  values.forEach(v => result[col].add(v));
}

function getDashboardFilters(formData?: FloorMapFormData): DashboardFilter[] {
  return (
    formData?.extraFormData?.filters ?? formData?.extra_form_data?.filters ?? []
  );
}

function getAdhocFilters(formData?: FloorMapFormData): AdhocFilter[] {
  return formData?.adhoc_filters ?? formData?.adhocFilters ?? [];
}

function getAdhocOperator(filter: AdhocFilter): string {
  return String(filter.operator ?? filter.operatorId ?? '').toUpperCase();
}

function toFiniteNumber(value: unknown): number | null {
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

function resolveHotThreshold(formData: FloorMapFormData | undefined): number {
  const candidates: unknown[] = [
    formData?.hot_threshold,
    formData?.hotThreshold,
    formData?.extraFormData?.hot_threshold,
    formData?.extraFormData?.hotThreshold,
    formData?.extra_form_data?.hot_threshold,
    formData?.extra_form_data?.hotThreshold,
  ];

  for (const value of candidates) {
    const parsed = toFiniteNumber(value);
    if (parsed !== null) return Math.min(Math.max(parsed, 0), 1);
  }

  return 0.96;
}

/**
 * Extract active filter values for unit_name and unit_group_name from the
 * extraFormData that Superset injects when a dashboard filter is applied,
 * and also from Explore adhoc_filters when viewing the chart in Explore.
 * Returns a map of { column -> Set<string> } for quick lookup.
 *
 * Note: These filters are applied client-side ONLY in polygon mode to the
 * Retail layer. In heatmap mode, all data is used for density calculation.
 */
function extractUnitFilterValues(
  formData: FloorMapFormData | undefined,
): UnitFilterValues {
  const result: UnitFilterValues = {};

  // Dashboard filters land in extraFormData.filters
  getDashboardFilters(formData).forEach(f => {
    const col: string = f.col ?? f.column ?? f.subject ?? '';
    addFilterValues(result, col, f.val ?? []);
  });

  // Explore adhoc filters live in formData.adhoc_filters.
  // Only include SIMPLE filters with inclusive operators, because this
  // client-side logic supports exact-value matching semantics.
  getAdhocFilters(formData).forEach(f => {
    if ((f.expressionType ?? '').toUpperCase() !== 'SIMPLE') return;

    const col = String(f.subject ?? '');
    const operator = getAdhocOperator(f);
    if (!INCLUSIVE_OPERATORS.has(operator)) return;

    addFilterValues(result, col, f.comparator ?? []);
  });

  return result;
}

export default function transformProps(chartProps: ChartProps) {
  const { width, height, formData, queriesData } = chartProps;
  const { floorSelection, floorImage } = formData;

  // Single query — all zones for the floor. In polygon mode, Retail layer is
  // filtered client-side by unit_name/unit_group_name dashboard filters.
  // In heatmap mode, all data is used without client-side filtering.
  const data = queriesData[0].data as TimeseriesDataRecord[];
  const unitFilterValues = extractUnitFilterValues(formData);

  return {
    width,
    height,
    data,
    unitFilterValues,
    floorSelection,
    floorImage: floorImage || floorSelection || '',
    hotThreshold: resolveHotThreshold(formData as FloorMapFormData),
  };
}
