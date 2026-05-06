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
import React, { useEffect, useState } from 'react';
import { css, styled } from '@superset-ui/core';
import {
  SupersetPluginChartCustomerJourneyProps,
  ProcessedJourney,
} from './types';
import useJourneyFilter from './hooks/useJourneyFilter';
import SearchBar from './components/SearchBar';
import JourneyTable from './components/JourneyTable';
import JourneyModal from './components/JourneyModal';

const Styles = styled.div`
  ${({ theme }) => css`
    font-family: ${theme.typography.families.sansSerif};
    height: 100%;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  `}
`;

export default function SupersetPluginChartCustomerJourney(
  props: SupersetPluginChartCustomerJourneyProps,
) {
  const { height, width, processedData, topSize } = props;

  const [selectedJourney, setSelectedJourney] =
    useState<ProcessedJourney | null>(null);

  const { selectedNodes, setSelectedNodes, availableNodes, topJourneys } =
    useJourneyFilter(processedData, topSize);

  // Reset modal when data changes (node/journey reset is handled inside the hook)
  useEffect(() => {
    setSelectedJourney(null);
  }, [processedData]);

  return (
    <Styles style={{ height, width }}>
      <SearchBar
        availableNodes={availableNodes}
        selectedNodes={selectedNodes}
        onNodesChange={setSelectedNodes}
      />
      <JourneyTable
        journeys={topJourneys}
        onJourneyClick={setSelectedJourney}
      />
      {selectedJourney && (
        <JourneyModal
          journey={selectedJourney}
          onClose={() => setSelectedJourney(null)}
        />
      )}
    </Styles>
  );
}
