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
import {
  RawJourneyRecord,
  ProcessedJourney,
  FlowPair,
  NodeStats,
  ProcessedData,
  JourneyMode,
} from '../types';

function normalizeJourneyMode(value: unknown): JourneyMode {
  if (value === 'unit_group') {
    return 'unit_group';
  }
  if (value === 'unit') {
    return 'unit';
  }
  if (Array.isArray(value) && value[0] === 'unit_group') {
    return 'unit_group';
  }
  if (
    typeof value === 'object' &&
    value !== null &&
    'value' in value &&
    (value as { value?: unknown }).value === 'unit_group'
  ) {
    return 'unit_group';
  }
  return 'unit';
}

function parseArrayString(arrayStr: string): number[] {
  try {
    return JSON.parse(arrayStr);
  } catch {
    return [];
  }
}

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

  // Support both JSON array strings and simple comma-delimited strings.
  if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
    try {
      const parsed = JSON.parse(trimmed);
      if (Array.isArray(parsed)) {
        return parsed
          .map(node => String(node).trim())
          .filter(node => node.length > 0);
      }
    } catch {
      // Fall back to comma split below.
    }
  }

  return trimmed
    .split(',')
    .map(n => n.trim())
    .filter(node => node.length > 0);
}

/**
 * For unit_group mode: collapse consecutive duplicate nodes and sum their dwell times.
 * Example:
 *   nodes: ['A', 'A', 'B', 'B', 'B', 'A']
 *   dwells: [1, 2, 3, 4, 5, 6]
 *   Result:
 *     collapsedNodes: ['A', 'B', 'A']
 *     collapsedDwells: [3, 12, 6]  (1+2, 3+4+5, 6)
 */
function collapseConsecutiveNodes(
  nodes: string[],
  dwellMins: number[],
): { collapsedNodes: string[]; collapsedDwells: number[] } {
  if (nodes.length === 0) {
    return { collapsedNodes: [], collapsedDwells: [] };
  }

  const collapsedNodes: string[] = [];
  const collapsedDwells: number[] = [];

  let currentNode = nodes[0];
  let currentDwell = dwellMins[0] || 0;

  for (let i = 1; i < nodes.length; i++) {
    if (nodes[i] === currentNode) {
      // Same as previous node - sum the dwell time
      currentDwell += dwellMins[i] || 0;
    } else {
      // Different node - push current and start new
      collapsedNodes.push(currentNode);
      collapsedDwells.push(currentDwell);
      currentNode = nodes[i];
      currentDwell = dwellMins[i] || 0;
    }
  }

  // Push the last node
  collapsedNodes.push(currentNode);
  collapsedDwells.push(currentDwell);

  return { collapsedNodes, collapsedDwells };
}

/**
 * Extract journey nodes and dwell times based on the selected mode.
 * For unit_group mode, also collapse consecutive duplicates.
 * Preserves the entry_count from before node collapsing.
 */
function extractJourneyData(
  record: RawJourneyRecord,
  mode: JourneyMode,
): {
  nodes: string[];
  dwellMins: number[];
  journeyKey: string;
  entryCount: number;
} {
  const rawNodes =
    mode === 'unit_group'
      ? record.journey_nodes_unit_group
      : record.journey_nodes_unit;

  const nodesList = parseNodeList(rawNodes);
  const dwellMins = parseArrayString(record.dwell_mins_per_node);
  const entryCount = record.entry_count ?? 1; // Default to 1 if not provided

  if (mode === 'unit_group') {
    const { collapsedNodes, collapsedDwells } = collapseConsecutiveNodes(
      nodesList,
      dwellMins,
    );
    return {
      nodes: collapsedNodes,
      dwellMins: collapsedDwells,
      journeyKey: collapsedNodes.join(', '),
      entryCount, // Preserve original entry count before collapsing
    };
  }

  return {
    nodes: nodesList,
    dwellMins,
    journeyKey: nodesList.join(', '),
    entryCount,
  };
}

function processJourneyData(
  rawData: RawJourneyRecord[],
  mode: JourneyMode,
): ProcessedData {
  const extractedRecords = rawData.map(record => ({
    clusterId: record.cluster_id,
    ...extractJourneyData(record, mode),
  }));

  // In unit_group mode, only keep collapsed journeys with more than 3 nodes.
  const recordsToProcess =
    mode === 'unit_group'
      ? extractedRecords.filter(record => record.nodes.length > 3)
      : extractedRecords;

  // Group by journey key (collapsed for unit_group mode)
  const journeyMap = new Map<
    string,
    {
      totalFootfall: number; // Sum of entry_count values
      clusterIds: Set<number>;
      dwellArrays: { dwell: number[]; count: number }[]; // Include entry count with each dwell array
      nodesList: string[]; // Store the collapsed nodes list
      clusterDetails: {
        clusterId: number;
        dwellMins: number[];
        entryCount: number;
      }[]; // Individual cluster details
    }
  >();

  recordsToProcess.forEach(
    ({ clusterId, nodes, dwellMins, journeyKey, entryCount }) => {
      if (!journeyMap.has(journeyKey)) {
        journeyMap.set(journeyKey, {
          totalFootfall: 0,
          clusterIds: new Set(),
          dwellArrays: [],
          nodesList: nodes,
          clusterDetails: [],
        });
      }

      const entry = journeyMap.get(journeyKey)!;
      entry.totalFootfall += entryCount; // Sum entry counts, don't just count records
      entry.clusterIds.add(clusterId);
      // Store individual cluster detail
      entry.clusterDetails.push({
        clusterId,
        dwellMins: dwellMins.length > 0 ? dwellMins : nodes.map(() => 0),
        entryCount,
      });
      if (dwellMins.length > 0) {
        entry.dwellArrays.push({ dwell: dwellMins, count: entryCount });
      }
    },
  );

  // Calculate aggregated journeys
  const journeys: ProcessedJourney[] = [];
  journeyMap.forEach((value, key) => {
    const nodesList = value.nodesList;
    const totalFootfall = value.totalFootfall;

    // Calculate average dwell times for each position (weighted by entry count)
    const maxLength =
      value.dwellArrays.length > 0
        ? Math.max(...value.dwellArrays.map(arr => arr.dwell.length))
        : nodesList.length;
    const avgDwellMins: number[] = [];
    for (let i = 0; i < maxLength; i++) {
      let totalDwell = 0;
      let totalCount = 0;
      value.dwellArrays.forEach(({ dwell, count }) => {
        if (dwell[i] !== undefined && !isNaN(dwell[i])) {
          totalDwell += dwell[i] * count;
          totalCount += count;
        }
      });
      avgDwellMins.push(totalCount > 0 ? totalDwell / totalCount : 0);
    }

    journeys.push({
      journeyNodes: key,
      nodesList,
      totalFootfall,
      avgDwellMins,
      clusterIds: Array.from(value.clusterIds),
      clusterDetails: value.clusterDetails,
    });
  });

  // Sort journeys by footfall descending
  journeys.sort((a, b) => b.totalFootfall - a.totalFootfall);

  // Generate flow pairs and node stats using extracted/collapsed data
  const flowCountMap = new Map<string, number>();
  const nodeFirstCount = new Map<string, number>();
  const nodeLastCount = new Map<string, number>();
  const nodeVisitCount = new Map<string, number>();
  const nodeDwellTimes = new Map<string, number[]>();

  // Unique customer tracking: cluster_id -> summed dwell time per node
  // Key: nodeName, Value: Map<clusterId, totalDwellAtNode>
  const nodeUniqueCustomerDwell = new Map<string, Map<number, number>>();
  // Unique customer flow tracking: "source|||target" -> Set<clusterId>
  const uniqueFlowCustomers = new Map<string, Set<number>>();

  recordsToProcess.forEach(({ clusterId, nodes, dwellMins, entryCount }) => {
    // Track first and last nodes (weighted by entry count)
    if (nodes.length > 0) {
      nodeFirstCount.set(
        nodes[0],
        (nodeFirstCount.get(nodes[0]) || 0) + entryCount,
      );
      nodeLastCount.set(
        nodes[nodes.length - 1],
        (nodeLastCount.get(nodes[nodes.length - 1]) || 0) + entryCount,
      );
    }

    // Track visits and dwell times per node (weighted by entry count)
    // Also track unique customer dwell times (summed per customer per node)
    const customerNodeDwell = new Map<string, number>(); // node -> summed dwell for this journey
    nodes.forEach((node, idx) => {
      nodeVisitCount.set(node, (nodeVisitCount.get(node) || 0) + entryCount);
      if (!nodeDwellTimes.has(node)) {
        nodeDwellTimes.set(node, []);
      }
      if (dwellMins[idx] !== undefined) {
        // Add dwell time repeated by entry count for proper averaging
        for (let i = 0; i < entryCount; i++) {
          nodeDwellTimes.get(node)!.push(dwellMins[idx]);
        }
        // Sum dwell time for this customer at this node in this journey
        customerNodeDwell.set(
          node,
          (customerNodeDwell.get(node) || 0) + dwellMins[idx],
        );
      }
    });

    // Update unique customer tracking per node
    customerNodeDwell.forEach((totalDwell, node) => {
      if (!nodeUniqueCustomerDwell.has(node)) {
        nodeUniqueCustomerDwell.set(node, new Map());
      }
      const customerMap = nodeUniqueCustomerDwell.get(node)!;
      // Sum dwell times for the same customer across different journeys
      customerMap.set(
        clusterId,
        (customerMap.get(clusterId) || 0) + totalDwell,
      );
    });

    // Track unique customer flows between nodes
    for (let i = 0; i < nodes.length - 1; i++) {
      const source = nodes[i];
      const target = nodes[i + 1];
      if (source !== target) {
        const key = `${source}|||${target}`;
        if (!uniqueFlowCustomers.has(key)) {
          uniqueFlowCustomers.set(key, new Set());
        }
        uniqueFlowCustomers.get(key)!.add(clusterId);
      }
    }

    // Generate pairs for flow analysis (consecutive different nodes after collapse)
    for (let i = 0; i < nodes.length - 1; i++) {
      const source = nodes[i];
      const target = nodes[i + 1];
      // In collapsed mode, adjacent nodes are always different
      if (source !== target) {
        const key = `${source}|||${target}`;
        flowCountMap.set(key, (flowCountMap.get(key) || 0) + entryCount);
      }
    }
  });

  // Convert flow pairs
  const flowPairs: FlowPair[] = [];
  flowCountMap.forEach((count, key) => {
    const [source, target] = key.split('|||');
    flowPairs.push({ source, target, count });
  });
  flowPairs.sort((a, b) => b.count - a.count);

  // Calculate incoming/outgoing flows per node
  const incomingFlows = new Map<string, Map<string, number>>();
  const outgoingFlows = new Map<string, Map<string, number>>();

  flowPairs.forEach(({ source, target, count }) => {
    if (!outgoingFlows.has(source)) {
      outgoingFlows.set(source, new Map());
    }
    outgoingFlows.get(source)!.set(target, count);

    if (!incomingFlows.has(target)) {
      incomingFlows.set(target, new Map());
    }
    incomingFlows.get(target)!.set(source, count);
  });

  // Calculate unique customer incoming/outgoing flows per node
  const uniqueIncomingFlows = new Map<string, Map<string, number>>();
  const uniqueOutgoingFlows = new Map<string, Map<string, number>>();

  uniqueFlowCustomers.forEach((customerSet, key) => {
    const [source, target] = key.split('|||');
    const uniqueCount = customerSet.size;

    if (!uniqueOutgoingFlows.has(source)) {
      uniqueOutgoingFlows.set(source, new Map());
    }
    uniqueOutgoingFlows.get(source)!.set(target, uniqueCount);

    if (!uniqueIncomingFlows.has(target)) {
      uniqueIncomingFlows.set(target, new Map());
    }
    uniqueIncomingFlows.get(target)!.set(source, uniqueCount);
  });

  // Build node stats
  const nodeStats = new Map<string, NodeStats>();
  const totalJourneys = Array.from(journeyMap.values()).reduce(
    (sum, entry) => sum + entry.totalFootfall,
    0,
  );

  nodeVisitCount.forEach((visits, nodeName) => {
    const firstCount = nodeFirstCount.get(nodeName) || 0;
    const lastCount = nodeLastCount.get(nodeName) || 0;
    const dwellTimes = nodeDwellTimes.get(nodeName) || [];
    const avgDwellTime =
      dwellTimes.length > 0
        ? dwellTimes.reduce((a, b) => a + b, 0) / dwellTimes.length
        : 0;

    const incoming = incomingFlows.get(nodeName) || new Map();
    const totalIncoming = Array.from(incoming.values()).reduce(
      (a, b) => a + b,
      0,
    );
    const incomingArr = Array.from(incoming.entries())
      .map(([source, count]) => ({
        source,
        count,
        percentage: totalIncoming > 0 ? (count / totalIncoming) * 100 : 0,
      }))
      .sort((a, b) => b.count - a.count);

    const outgoing = outgoingFlows.get(nodeName) || new Map();
    const totalOutgoing = Array.from(outgoing.values()).reduce(
      (a, b) => a + b,
      0,
    );
    const outgoingArr = Array.from(outgoing.entries())
      .map(([target, count]) => ({
        target,
        count,
        percentage: totalOutgoing > 0 ? (count / totalOutgoing) * 100 : 0,
      }))
      .sort((a, b) => b.count - a.count);

    // Unique customer stats
    const customerDwellMap = nodeUniqueCustomerDwell.get(nodeName) || new Map();
    const uniqueCustomers = customerDwellMap.size;
    const customerDwellValues = Array.from(customerDwellMap.values());
    const avgDwellTimePerCustomer =
      customerDwellValues.length > 0
        ? customerDwellValues.reduce((a, b) => a + b, 0) /
          customerDwellValues.length
        : 0;

    // Unique customer incoming flows
    const uniqueIncoming = uniqueIncomingFlows.get(nodeName) || new Map();
    const totalUniqueIncoming = Array.from(uniqueIncoming.values()).reduce(
      (a, b) => a + b,
      0,
    );
    const uniqueIncomingArr = Array.from(uniqueIncoming.entries())
      .map(([source, count]) => ({
        source,
        count,
        percentage:
          totalUniqueIncoming > 0 ? (count / totalUniqueIncoming) * 100 : 0,
      }))
      .sort((a, b) => b.count - a.count);

    // Unique customer outgoing flows
    const uniqueOutgoing = uniqueOutgoingFlows.get(nodeName) || new Map();
    const totalUniqueOutgoing = Array.from(uniqueOutgoing.values()).reduce(
      (a, b) => a + b,
      0,
    );
    const uniqueOutgoingArr = Array.from(uniqueOutgoing.entries())
      .map(([target, count]) => ({
        target,
        count,
        percentage:
          totalUniqueOutgoing > 0 ? (count / totalUniqueOutgoing) * 100 : 0,
      }))
      .sort((a, b) => b.count - a.count);

    nodeStats.set(nodeName, {
      nodeName,
      totalVisits: visits,
      asFirstNode: firstCount,
      asLastNode: lastCount,
      firstNodePercentage:
        totalJourneys > 0 ? (firstCount / totalJourneys) * 100 : 0,
      lastNodePercentage:
        totalJourneys > 0 ? (lastCount / totalJourneys) * 100 : 0,
      incomingFlows: incomingArr,
      outgoingFlows: outgoingArr,
      avgDwellTime,
      uniqueCustomers,
      avgDwellTimePerCustomer,
      uniqueIncomingFlows: uniqueIncomingArr,
      uniqueOutgoingFlows: uniqueOutgoingArr,
    });
  });

  return {
    journeys,
    flowPairs,
    nodeStats,
    totalJourneys,
  };
}

export default function transformProps(chartProps: ChartProps) {
  const { width, height, formData, queriesData } = chartProps;
  const { boldText, headerFontSize, headerText } = formData;
  const data = queriesData[0].data as TimeseriesDataRecord[];

  const journeyMode = normalizeJourneyMode(
    (formData as Record<string, unknown>).journey_mode ??
      (formData as Record<string, unknown>).journeyMode,
  );

  // Process the raw journey data with the selected mode
  const processedData = processJourneyData(
    data as unknown as RawJourneyRecord[],
    journeyMode,
  );

  return {
    width,
    height,
    data,
    processedData,
    journeyMode,
    boldText,
    headerFontSize,
    headerText,
  };
}
