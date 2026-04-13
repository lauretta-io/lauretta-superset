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

export interface SupersetPluginChartFloorMapStylesProps {
  height: number;
  width: number;
  headerFontSize: keyof typeof supersetTheme.typography.sizes;
  boldText: boolean;
}

export type ViewMode = 'polygon' | 'heatmap';

interface SupersetPluginChartFloorMapCustomizeProps {
  headerText: string;
  floorSelection: string;
  floorImage: string;
}

export type SupersetPluginChartFloorMapQueryFormData = QueryFormData &
  SupersetPluginChartFloorMapStylesProps &
  SupersetPluginChartFloorMapCustomizeProps;

export type SupersetPluginChartFloorMapProps =
  SupersetPluginChartFloorMapStylesProps &
    SupersetPluginChartFloorMapCustomizeProps & {
      /** Full floor dataset with all zones (Retail, Entrances, Circulation, Public).
       *  In polygon mode, client-side filters apply to Retail layer only.
       *  In heatmap mode, all data is used for density calculation without filtering.
       */
      data: TimeseriesDataRecord[];
      /**
       * Active dashboard filter values for unit_name / unit_group_name columns.
       * Applied client-side in polygon mode to the Retail layer only, so
       * non-unit zones (Entrances, Circulation, Public) are never hidden.
       * Map of { columnName -> Set<filterValue> }; empty map means no filter active.
       */
      unitFilterValues: Record<string, Set<string>>;
    };
