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
import { ChartProps, supersetTheme } from '@superset-ui/core';
import transformProps from '../../src/plugin/transformProps';

describe('SupersetPluginChartCustomerJourney transformProps', () => {
  const formData = {
    colorScheme: 'bnbColors',
    datasource: '3__table',
    granularity_sqla: 'ds',
    metric: 'sum__num',
    series: 'name',
    boldText: true,
    headerFontSize: 'xs',
    headerText: 'my text',
  };

  const mockJourneyData = [
    {
      cluster_id: 537789325,
      journey_nodes: 'Store A,Store B,Store C',
      dwell_mins_per_node: '[10, 20, 30]',
    },
    {
      cluster_id: 537787883,
      journey_nodes: 'Store A,Store B,Store C',
      dwell_mins_per_node: '[15, 25, 35]',
    },
  ];

  const chartProps = new ChartProps({
    formData,
    width: 800,
    height: 600,
    theme: supersetTheme,
    queriesData: [
      {
        data: mockJourneyData,
      },
    ],
  });

  it('should transform chart props with processedData', () => {
    const result = transformProps(chartProps);

    expect(result.width).toEqual(800);
    expect(result.height).toEqual(600);
    expect(result.boldText).toEqual(true);
    expect(result.headerFontSize).toEqual('xs');
    expect(result.headerText).toEqual('my text');
    expect(result.processedData).toBeDefined();
    expect(result.processedData.journeys).toHaveLength(1);
    expect(result.processedData.journeys[0].totalFootfall).toEqual(2);
  });

  it('should calculate average dwell times correctly', () => {
    const result = transformProps(chartProps);
    const journey = result.processedData.journeys[0];

    expect(journey.avgDwellMins[0]).toEqual(12.5); // (10 + 15) / 2
    expect(journey.avgDwellMins[1]).toEqual(22.5); // (20 + 25) / 2
    expect(journey.avgDwellMins[2]).toEqual(32.5); // (30 + 35) / 2
  });

  it('should generate flow pairs correctly', () => {
    const result = transformProps(chartProps);

    expect(result.processedData.flowPairs.length).toBeGreaterThan(0);
    expect(
      result.processedData.flowPairs.some(
        p => p.source === 'Store A' && p.target === 'Store B',
      ),
    ).toBe(true);
  });
});
