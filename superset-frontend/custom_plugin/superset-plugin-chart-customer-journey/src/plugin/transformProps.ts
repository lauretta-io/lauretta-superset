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
import { ChartProps } from '@superset-ui/core';
import { RawJourneyRecord, ProcessedJourney, ProcessedData } from '../types';

function parseNodeList(rawNodes: unknown): string[] {
  if (Array.isArray(rawNodes)) {
    return rawNodes
      .map(node => String(node).trim())
      .filter(node => node.length > 0);
  }

  if (typeof rawNodes !== 'string') {
    return [];
  }

  const trimmed = rawNodes.trim();
  if (!trimmed) {
    return [];
  }

  if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
    try {
      const parsed = JSON.parse(trimmed);
      if (Array.isArray(parsed)) {
        return parsed
          .map(node => String(node).trim())
          .filter(node => node.length > 0);
      }
    } catch {
      // Fall back to comma split
    }
  }

  return trimmed
    .split(',')
    .map(n => n.trim())
    .filter(node => node.length > 0);
}

function processJourneyData(rawData: RawJourneyRecord[]): ProcessedData {
  const journeys: ProcessedJourney[] = [];
  const uniqueNodesSet = new Set<string>();

  rawData.forEach(record => {
    const nodesList = parseNodeList(record.journey_nodes);
    const journeyCount = Number(record.journey_count);

    if (nodesList.length === 0) return;

    const journeyKey = nodesList.join(', ');

    journeys.push({
      journeyNodes: journeyKey,
      nodesList,
      journeyCount: Number.isFinite(journeyCount) ? journeyCount : 0,
    });

    nodesList.forEach(node => uniqueNodesSet.add(node));
  });

  // Sort journeys by count descending
  journeys.sort((a, b) => b.journeyCount - a.journeyCount);

  return {
    journeys,
    uniqueNodes: Array.from(uniqueNodesSet).sort(),
  };
}

function parseTopSize(value: unknown, fallback = 20): number {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return Math.max(1, Math.floor(value));
  }

  if (typeof value === 'string') {
    const parsed = parseInt(value, 10);
    return Number.isNaN(parsed) ? fallback : Math.max(1, parsed);
  }

  if (Array.isArray(value) && value.length > 0) {
    return parseTopSize(value[0], fallback);
  }

  if (value && typeof value === 'object' && 'value' in value) {
    return parseTopSize((value as { value?: unknown }).value, fallback);
  }

  return fallback;
}

export default function transformProps(chartProps: ChartProps) {
  const { width, height, formData, queriesData } = chartProps;
  const data = queriesData[0].data as RawJourneyRecord[];
  const formDataRecord = formData as Record<string, unknown>;
  const rawTopSize = formDataRecord.topSize ?? formDataRecord.top_size;

  const topSize = parseTopSize(rawTopSize, 20);

  const processedData = processJourneyData(data);

  return {
    width,
    height,
    processedData,
    topSize,
  };
}
