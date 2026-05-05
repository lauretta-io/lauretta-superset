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

function normalizeSearchValue(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

function getEditDistance(source: string, target: string): number {
  const matrix = Array.from({ length: source.length + 1 }, () =>
    Array<number>(target.length + 1).fill(0),
  );

  for (let i = 0; i <= source.length; i += 1) {
    matrix[i][0] = i;
  }
  for (let j = 0; j <= target.length; j += 1) {
    matrix[0][j] = j;
  }

  for (let i = 1; i <= source.length; i += 1) {
    for (let j = 1; j <= target.length; j += 1) {
      const cost = source[i - 1] === target[j - 1] ? 0 : 1;
      matrix[i][j] = Math.min(
        matrix[i - 1][j] + 1,
        matrix[i][j - 1] + 1,
        matrix[i - 1][j - 1] + cost,
      );
    }
  }

  return matrix[source.length][target.length];
}

export function matchesSearchQuery(node: string, query: string): boolean {
  const normalizedNode = normalizeSearchValue(node);
  const normalizedQuery = normalizeSearchValue(query);

  if (!normalizedQuery) return true;
  if (normalizedNode.includes(normalizedQuery)) return true;

  const queryTokens = normalizedQuery.split(' ').filter(Boolean);
  const nodeTokens = normalizedNode.split(' ').filter(Boolean);

  return queryTokens.every(queryToken =>
    nodeTokens.some(nodeToken => {
      if (nodeToken.startsWith(queryToken) || nodeToken.includes(queryToken)) {
        return true;
      }
      if (
        queryToken.length >= 4 &&
        Math.abs(nodeToken.length - queryToken.length) <= 1
      ) {
        return getEditDistance(nodeToken, queryToken) <= 1;
      }
      return false;
    }),
  );
}
