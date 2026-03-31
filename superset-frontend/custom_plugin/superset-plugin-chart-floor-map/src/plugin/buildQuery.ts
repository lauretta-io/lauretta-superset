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

/** Strip adhoc filters */
function stripUnitAdhocFilters(filters: any[] = []): any[] {
  return filters.filter((f: any) => {
    const col: string = f.subject ?? f.col ?? f.column ?? '';
    return !UNIT_FILTER_COLUMNS.has(col);
  });
}

/** 🔥 Strip dashboard/native filters (IMPORTANT for refresh issue) */
function stripUnitExtraFilters(filters: any[] = []): any[] {
  return filters.filter((f: any) => {
    const col: string = f.col ?? f.column ?? f.subject ?? '';
    return !UNIT_FILTER_COLUMNS.has(col);
  });
}

export default function buildQuery(formData: QueryFormData) {
  const { cols: groupby } = formData;

  const hasGroupby = groupby && Array.isArray(groupby) && groupby.length > 0;

  const baseMetrics = [
    {
      expressionType: 'SQL' as const,
      sqlExpression: 'COUNT(*)',
      label: '_dummy_metric',
    },
  ];

  return buildQueryContext(formData, baseQueryObject => {
    // 🔍 DEBUG: inspect incoming filters BEFORE stripping
    console.log('🔍 BASE QUERY OBJECT (before strip)', {
      filters: baseQueryObject.filters,
      adhoc_filters: baseQueryObject.adhoc_filters,
      extra_filters: baseQueryObject.extra_filters,
    });

    const strippedFilters = stripUnitFilters(baseQueryObject.filters ?? []);
    const strippedAdhoc = stripUnitAdhocFilters(
      baseQueryObject.adhoc_filters ?? [],
    );
    const strippedExtra = stripUnitExtraFilters(
      baseQueryObject.extra_filters ?? [],
    );

    // 🔍 DEBUG: inspect AFTER stripping
    console.log('🧹 STRIPPED QUERY OBJECT (query 1)', {
      filters: strippedFilters,
      adhoc_filters: strippedAdhoc,
      extra_filters: strippedExtra,
    });

    return [
      // ── Query 0: FULLY FILTERED ──────────────────────────────────────────
      {
        ...baseQueryObject,
        groupby: hasGroupby ? groupby : [],
        metrics: baseMetrics,
      },

      // ── Query 1: UNIT-FILTER-STRIPPED ────────────────────────────────────
      {
        ...baseQueryObject,
        groupby: hasGroupby ? groupby : [],
        metrics: baseMetrics,

        // ✅ Keep everything except unit filters
        filters: strippedFilters,
        adhoc_filters: strippedAdhoc,
        extra_filters: strippedExtra,
      },
    ];
  });
}
