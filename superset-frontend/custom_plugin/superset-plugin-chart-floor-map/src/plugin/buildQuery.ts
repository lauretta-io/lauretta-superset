/**
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements...
 */
import { buildQueryContext, QueryFormData } from '@superset-ui/core';

/**
 * Columns that are applied as client-side filters in the chart component
 * (polygon Retail layer only) and must be STRIPPED from the server query so
 * that the dataset always returns ALL zones for the floor.  Non-unit zones
 * (Entrances, Circulation, Public) are therefore never excluded by these
 * filters at the query level.
 */
const UNIT_FILTER_COLUMNS = new Set(['unit_name', 'unit_group_name']);

/** Strip unit-scoped filters from any filter array. */
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
    // Strip unit_name / unit_group_name from ALL filter arrays so the single
    // query always returns every zone on the floor.  The chart component reads
    // the active filter values from formData.extraFormData and applies them
    // client-side to the Retail layer only.
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
      // ── Single query: unit filters stripped, all other filters preserved ──
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
