/**
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements...
 */
import { buildQueryContext, QueryFormData } from '@superset-ui/core';

/**
 * Columns that belong to unit/unit_group filters which should be STRIPPED
 * from query 1 (full-floor heatmap). All other filters (floor_id, time range,
 * temporal adhoc filters) are preserved in both queries.
 */
const UNIT_FILTER_COLUMNS = new Set(['unit_name', 'unit_group_name']);

/** Strip standard filters */
function stripUnitFilters(filters: any[] = []): any[] {
  return filters.filter((f: any) => {
    const col: string = f.col ?? f.column ?? f.subject ?? '';
    return !UNIT_FILTER_COLUMNS.has(col);
  });
}

export default function buildQuery(formData: QueryFormData) {
  const { cols: groupby } = formData;

  const hasGroupby = groupby && Array.isArray(groupby) && groupby.length > 0;

  /**
   * Default dummy metric: COUNT(*)
   * This ensures Superset always executes a query with an aggregate function,
   * so even when filters return no data, the query structure is valid and the
   * floor map base image will display.
   */
  const defaultMetrics = [
    {
      expressionType: 'SQL' as const,
      sqlExpression: 'COUNT(*)',
      label: '_dummy_metric',
    },
  ];

  return buildQueryContext(formData, baseQueryObject => {
    const strippedFilters = stripUnitFilters(baseQueryObject.filters ?? []);
    const adhocRaw = baseQueryObject.adhoc_filters;
    const strippedAdhoc = stripUnitFilters(
      Array.isArray(adhocRaw) ? adhocRaw : [],
    );
    const extraRaw = baseQueryObject.extra_filters;
    const strippedExtra = stripUnitFilters(
      Array.isArray(extraRaw) ? extraRaw : [],
    );

    return [
      // ── Query 0: FULLY FILTERED (polygon mode + store list) ──────────
      {
        ...baseQueryObject,
        groupby: hasGroupby ? groupby : [],
        metrics: defaultMetrics,
      },

      // ── Query 1: UNIT-FILTER-STRIPPED (heatmap — full floor) ─────────
      {
        ...baseQueryObject,
        groupby: hasGroupby ? groupby : [],
        metrics: defaultMetrics,
        filters: strippedFilters,
        adhoc_filters: strippedAdhoc,
        extra_filters: strippedExtra,
      },
    ];
  });
}
