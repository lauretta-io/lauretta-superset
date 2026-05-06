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
import { useEffect, useMemo, useState } from 'react';
import { ProcessedData, ProcessedJourney } from '../types';

interface UseJourneyFilterResult {
  selectedNodes: string[];
  setSelectedNodes: React.Dispatch<React.SetStateAction<string[]>>;
  availableNodes: string[];
  topJourneys: ProcessedJourney[];
}

export default function useJourneyFilter(
  processedData: ProcessedData | undefined,
  topSize: number,
): UseJourneyFilterResult {
  const [selectedNodes, setSelectedNodes] = useState<string[]>([]);

  // Reset selection whenever the underlying data changes
  useEffect(() => {
    setSelectedNodes([]);
  }, [processedData]);

  const { availableNodes, topJourneys } = useMemo(() => {
    const journeys = processedData?.journeys ?? [];
    const selectedNodesLower = selectedNodes.map(n => n.toLowerCase().trim());

    let filtered: ProcessedJourney[];

    if (selectedNodes.length === 0) {
      filtered = [...journeys];
    } else {
      // AND logic: journey must contain ALL selected nodes
      filtered = journeys.filter(journey => {
        const journeyNodesLower = journey.nodesList.map(n =>
          n.toLowerCase().trim(),
        );
        return selectedNodesLower.every(selected =>
          journeyNodesLower.some(jNode => jNode === selected),
        );
      });
    }

    filtered.sort((a, b) => b.journeyCount - a.journeyCount);

    // Available nodes for search: from filtered journeys, excluding already selected
    const nodesSet = new Set<string>();
    filtered.forEach(journey => {
      journey.nodesList.forEach(node => {
        if (!selectedNodesLower.includes(node.toLowerCase().trim())) {
          nodesSet.add(node);
        }
      });
    });

    return {
      availableNodes: Array.from(nodesSet).sort(),
      topJourneys: filtered.slice(0, topSize),
    };
  }, [processedData, selectedNodes, topSize]);

  return { selectedNodes, setSelectedNodes, availableNodes, topJourneys };
}
