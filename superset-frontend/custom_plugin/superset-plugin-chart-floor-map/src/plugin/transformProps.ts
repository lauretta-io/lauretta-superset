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

/** Columns whose dashboard filter values are applied client-side (polygon Retail layer only). */
const UNIT_FILTER_COLUMNS = new Set(['unit_name', 'unit_group_name']);

/**
 * Extract active filter values for unit_name / unit_group_name from the
 * extraFormData that Superset injects when a dashboard filter is applied.
 * Returns a map of { column -> Set<string> } for quick look-up.
 */
function extractUnitFilterValues(
  formData: any,
): Record<string, Set<string>> {
  const result: Record<string, Set<string>> = {};

  // Dashboard filters land in extraFormData.filters
  const extraFilters: any[] = formData?.extraFormData?.filters ?? [];
  extraFilters.forEach((f: any) => {
    const col: string = f.col ?? f.column ?? f.subject ?? '';
    if (UNIT_FILTER_COLUMNS.has(col)) {
      const values: string[] = Array.isArray(f.val) ? f.val : [f.val];
      if (!result[col]) result[col] = new Set<string>();
      values.forEach(v => result[col].add(String(v)));
    }
  });

  return result;
}

export default function transformProps(chartProps: ChartProps) {
  const { width, height, formData, queriesData } = chartProps;
  const { boldText, headerFontSize, headerText, floorSelection, floorImage } =
    formData;

  // Single query — all zones for the floor (unit filters are stripped at query
  // level and re-applied client-side for the Retail polygon layer only).
  const data = queriesData[0].data as TimeseriesDataRecord[];

  // Extract unit_name / unit_group_name filter values from the dashboard context
  // so the chart component can apply them client-side to the Retail layer.
  const unitFilterValues = extractUnitFilterValues(formData);

  return {
    width,
    height,
    data,
    unitFilterValues,
    boldText,
    headerFontSize,
    headerText,
    floorSelection,
    floorImage: floorImage || floorSelection || '',
  };
}
