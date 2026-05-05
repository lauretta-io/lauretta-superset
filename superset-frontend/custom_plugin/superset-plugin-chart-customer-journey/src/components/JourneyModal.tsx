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
import React, { useEffect } from 'react';
import { css, styled } from '@superset-ui/core';
import { ProcessedJourney } from '../types';
import PersonIcon from './PersonIcon';

const Styles = styled.div`
  ${({ theme }) => css`
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0, 0, 0, 0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
    padding: ${theme.gridUnit * 5}px;

    .modal-content {
      background: ${theme.colors.grayscale.light5};
      border-radius: ${theme.gridUnit * 2}px;
      width: min(96vw, 900px);
      max-height: 90vh;
      overflow-y: auto;
      box-shadow: 0 12px 40px rgba(0, 0, 0, 0.15);
      position: relative;
    }

    .modal-header {
      padding: ${theme.gridUnit * 4}px ${theme.gridUnit * 8}px
        ${theme.gridUnit * 4}px ${theme.gridUnit * 5}px;
      border-bottom: 1px solid ${theme.colors.grayscale.light2};
      display: flex;
      justify-content: flex-start;
      align-items: center;
      background: ${theme.colors.grayscale.light4};
    }

    .modal-title {
      font-size: ${theme.typography.sizes.l}px;
      font-weight: ${theme.typography.weights.bold};
      color: ${theme.colors.grayscale.dark2};
      margin: 0;
    }

    .close-btn {
      position: absolute;
      top: ${theme.gridUnit * 2}px;
      right: ${theme.gridUnit * 2}px;
      width: 34px;
      height: 34px;
      border-radius: 50%;
      border: 1px solid ${theme.colors.grayscale.light2};
      background: rgba(255, 255, 255, 0.92);
      font-size: 22px;
      color: ${theme.colors.grayscale.dark1};
      cursor: pointer;
      padding: 0;
      line-height: 1;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      z-index: 2;
    }

    .close-btn:hover {
      background: white;
      border-color: ${theme.colors.primary.light1};
      color: ${theme.colors.primary.base};
    }

    .modal-body {
      padding: ${theme.gridUnit * 5}px;
    }

    .count-pill {
      display: inline-flex;
      align-items: center;
      gap: ${theme.gridUnit}px;
      padding: ${theme.gridUnit * 2}px ${theme.gridUnit * 3}px;
      background: ${theme.colors.primary.light5};
      border: 1px solid ${theme.colors.primary.light2};
      border-radius: ${theme.gridUnit * 2}px;
      color: ${theme.colors.primary.dark1};
      font-size: ${theme.typography.sizes.m}px;
      font-weight: ${theme.typography.weights.bold};
      margin-bottom: ${theme.gridUnit * 4}px;
    }

    .journey-path {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: ${theme.gridUnit * 2}px;
      padding: ${theme.gridUnit * 4}px;
      background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);
      border: 1px solid ${theme.colors.grayscale.light2};
      border-radius: ${theme.gridUnit * 2}px;
    }

    .path-node {
      display: inline-flex;
      align-items: center;
      gap: ${theme.gridUnit}px;
      padding: ${theme.gridUnit * 2}px ${theme.gridUnit * 3}px;
      background: ${theme.colors.primary.light4};
      border: 1px solid ${theme.colors.primary.light2};
      border-radius: ${theme.gridUnit * 2}px;
      font-size: ${theme.typography.sizes.m}px;
      color: ${theme.colors.primary.dark1};
      font-weight: ${theme.typography.weights.medium};
      box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
    }

    .path-node-number {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 22px;
      height: 22px;
      border-radius: 50%;
      background: ${theme.colors.primary.base};
      color: white;
      font-size: ${theme.typography.sizes.s}px;
      font-weight: ${theme.typography.weights.bold};
    }

    .path-connector {
      color: ${theme.colors.grayscale.base};
      font-size: ${theme.typography.sizes.l}px;
      font-weight: ${theme.typography.weights.bold};
    }
  `}
`;

interface JourneyModalProps {
  journey: ProcessedJourney;
  onClose: () => void;
}

export default function JourneyModal({ journey, onClose }: JourneyModalProps) {
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  return (
    <Styles onClick={onClose}>
      <div className="modal-content" onClick={e => e.stopPropagation()}>
        <button type="button" className="close-btn" onClick={onClose}>
          ×
        </button>
        <div className="modal-header">
          <h3 className="modal-title">Full Journey Path</h3>
        </div>
        <div className="modal-body">
          <div className="count-pill">
            <PersonIcon />
            Journey Count: {journey.journeyCount.toLocaleString()}
          </div>
          <div className="journey-path">
            {journey.nodesList.map((node, index) => (
              // eslint-disable-next-line react/no-array-index-key
              <React.Fragment key={`path-${node}-${index}`}>
                {index > 0 && <span className="path-connector">→</span>}
                <div className="path-node">
                  <span className="path-node-number">{index + 1}</span>
                  <span>{node}</span>
                </div>
              </React.Fragment>
            ))}
          </div>
        </div>
      </div>
    </Styles>
  );
}
