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
import { QueryFormData } from '@superset-ui/core';

export interface SupersetPluginChartCustomerJourneyStylesProps {
  height: number;
  width: number;
}

export type SupersetPluginChartCustomerJourneyQueryFormData = QueryFormData &
  SupersetPluginChartCustomerJourneyStylesProps;

// Raw data record from query
export interface RawJourneyRecord {
  journey_nodes: string;
  total_footfall: number;
}

// Processed journey with aggregated data
export interface ProcessedJourney {
  journeyNodes: string;
  nodesList: string[];
  totalFootfall: number;
}

// All processed data passed to the component
export interface ProcessedData {
  journeys: ProcessedJourney[];
  uniqueNodes: string[];
}

export type SupersetPluginChartCustomerJourneyProps =
  SupersetPluginChartCustomerJourneyStylesProps & {
    processedData: ProcessedData;
    topSize: number;
  };
