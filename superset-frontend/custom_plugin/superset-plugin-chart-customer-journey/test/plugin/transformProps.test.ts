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
    topSize: 20,
  };

  const mockJourneyData = [
    {
      journey_nodes: 'Store A,Store B,Store C',
      journey_count: 150,
    },
    {
      journey_nodes: 'Store A,Store D',
      journey_count: 80,
    },
    {
      journey_nodes: 'Store B,Store C,Store D,Store E',
      journey_count: 45,
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
    expect(result.topSize).toEqual(20);
    expect(result.processedData).toBeDefined();
    expect(result.processedData.journeys).toHaveLength(3);
  });

  it('should parse journey nodes correctly', () => {
    const result = transformProps(chartProps);
    const firstJourney = result.processedData.journeys[0];

    expect(firstJourney.nodesList).toEqual(['Store A', 'Store B', 'Store C']);
    expect(firstJourney.journeyNodes).toEqual('Store A, Store B, Store C');
    expect(firstJourney.journeyCount).toEqual(150);
  });

  it('should sort journeys by journey count descending', () => {
    const result = transformProps(chartProps);
    const journeys = result.processedData.journeys;

    expect(journeys[0].journeyCount).toEqual(150);
    expect(journeys[1].journeyCount).toEqual(80);
    expect(journeys[2].journeyCount).toEqual(45);
  });

  it('should collect unique nodes', () => {
    const result = transformProps(chartProps);
    const uniqueNodes = result.processedData.uniqueNodes;

    expect(uniqueNodes).toContain('Store A');
    expect(uniqueNodes).toContain('Store B');
    expect(uniqueNodes).toContain('Store C');
    expect(uniqueNodes).toContain('Store D');
    expect(uniqueNodes).toContain('Store E');
    expect(uniqueNodes).toHaveLength(5);
  });

  it('should handle JSON array format for journey_nodes', () => {
    const jsonArrayData = [
      {
        journey_nodes: '["Zone 1", "Zone 2", "Zone 3"]',
        journey_count: 100,
      },
    ];

    const jsonChartProps = new ChartProps({
      formData,
      width: 800,
      height: 600,
      theme: supersetTheme,
      queriesData: [{ data: jsonArrayData }],
    });

    const result = transformProps(jsonChartProps);
    expect(result.processedData.journeys[0].nodesList).toEqual([
      'Zone 1',
      'Zone 2',
      'Zone 3',
    ]);
  });

  it('should use default topSize when not provided', () => {
    const noTopSizeFormData = { ...formData };
    delete (noTopSizeFormData as Record<string, unknown>).topSize;

    const noTopSizeChartProps = new ChartProps({
      formData: noTopSizeFormData,
      width: 800,
      height: 600,
      theme: supersetTheme,
      queriesData: [{ data: mockJourneyData }],
    });

    const result = transformProps(noTopSizeChartProps);
    expect(result.topSize).toEqual(20);
  });
});
