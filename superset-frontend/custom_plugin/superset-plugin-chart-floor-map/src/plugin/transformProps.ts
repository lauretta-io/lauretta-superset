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

interface DashboardFilter {
  col?: string;
  column?: string;
  subject?: string;
  val?: unknown | unknown[];
}

interface FloorMapFormData {
  extraFormData?: {
    filters?: DashboardFilter[];
  };
}

function toStringArray(value: unknown | unknown[]): string[] {
  const items = Array.isArray(value) ? value : [value];
  return items.filter(v => v !== null && v !== undefined).map(v => String(v));
}

/**
 * Extract active filter values for unit_name and unit_group_name from the
 * extraFormData that Superset injects when a dashboard filter is applied.
 * Returns a map of { column -> Set<string> } for quick lookup.
 *
 * Note: These filters are applied client-side ONLY in polygon mode to the
 * Retail layer. In heatmap mode, all data is used for density calculation.
 */
function extractUnitFilterValues(
  formData: FloorMapFormData | undefined,
): Record<string, Set<string>> {
  const result: Record<string, Set<string>> = {};

  // Dashboard filters land in extraFormData.filters
  const extraFilters = formData?.extraFormData?.filters ?? [];
  extraFilters.forEach(f => {
    const col: string = f.col ?? f.column ?? f.subject ?? '';
    if (UNIT_FILTER_COLUMNS.has(col)) {
      const values = toStringArray(f.val ?? []);
      if (!result[col]) result[col] = new Set<string>();
      values.forEach(v => result[col].add(v));
    }
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
  };
}
