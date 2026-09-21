/**
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements...
 */
import { buildQueryContext, QueryFormData } from '@superset-ui/core';

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
    return [
      // ── Single query with default metric and existing dashboard filters ──
      {
        ...baseQueryObject,
        groupby: hasGroupby ? groupby : [],
        metrics: defaultMetrics,
      },
    ];
  });
}
