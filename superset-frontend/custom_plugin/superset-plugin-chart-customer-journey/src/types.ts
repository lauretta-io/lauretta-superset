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
import {
  QueryFormData,
  supersetTheme,
  TimeseriesDataRecord,
} from '@superset-ui/core';

export interface SupersetPluginChartCustomerJourneyStylesProps {
  height: number;
  width: number;
  headerFontSize: keyof typeof supersetTheme.typography.sizes;
  boldText: boolean;
}

interface SupersetPluginChartCustomerJourneyCustomizeProps {
  headerText: string;
}

export type SupersetPluginChartCustomerJourneyQueryFormData = QueryFormData &
  SupersetPluginChartCustomerJourneyStylesProps &
  SupersetPluginChartCustomerJourneyCustomizeProps;

// Journey display mode
export type JourneyMode = 'unit' | 'unit_group';

// Raw data record from query
export interface RawJourneyRecord {
  cluster_id: number;
  journey_nodes_unit: string;
  journey_nodes_unit_group: string;
  dwell_mins_per_node: string;
  entry_count?: number; // Footfall count for this journey record
}

// Processed journey with aggregated data
export interface ProcessedJourney {
  journeyNodes: string;
  nodesList: string[];
  totalFootfall: number;
  avgDwellMins: number[];
  clusterIds: number[];
}

// Flow pair for source-target analysis
export interface FlowPair {
  source: string;
  target: string;
  count: number;
}

// Node statistics for individual node analysis
export interface NodeStats {
  nodeName: string;
  totalVisits: number;
  asFirstNode: number;
  asLastNode: number;
  firstNodePercentage: number;
  lastNodePercentage: number;
  incomingFlows: { source: string; count: number; percentage: number }[];
  outgoingFlows: { target: string; count: number; percentage: number }[];
  avgDwellTime: number;
}

// All processed data passed to the component
export interface ProcessedData {
  journeys: ProcessedJourney[];
  flowPairs: FlowPair[];
  nodeStats: Map<string, NodeStats>;
  totalJourneys: number;
}

export type SupersetPluginChartCustomerJourneyProps =
  SupersetPluginChartCustomerJourneyStylesProps &
    SupersetPluginChartCustomerJourneyCustomizeProps & {
      data: TimeseriesDataRecord[];
      processedData: ProcessedData;
      journeyMode: JourneyMode;
    };
