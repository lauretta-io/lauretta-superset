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
import React, { ReactNode } from 'react';
import { TransformWrapper, TransformComponent } from 'react-zoom-pan-pinch';
import { styled } from '@superset-ui/core';

const ZoomPanContainer = styled.div`
  width: 100%;
  height: 100%;
  overflow: hidden;
  background-color: #ffffff;
  position: relative;
  flex: 1;

  .react-transform-wrapper {
    width: 100%;
    height: 100%;
  }

  .react-transform-component {
    width: 100%;
    height: 100%;
  }

  svg {
    width: 100%;
    height: 100%;
    image {
      image-rendering: crisp-edges;
      image-rendering: pixelated;
    }
  }

  /* Zoom controls styling */
  .zoom-controls {
    position: absolute;
    top: 10px;
    right: 10px;
    z-index: 100;
    display: flex;
    gap: 4px;
    background: white;
    padding: 6px;
    border-radius: 4px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
  }

  button {
    padding: 0;
    border: 1px solid #d9d9d9;
    background: white;
    border-radius: 2px;
    cursor: pointer;
    width: 28px;
    height: 28px;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.3s ease;
    color: #595959;

    &:hover {
      color: #40a9ff;
      border-color: #40a9ff;
    }

    &:active {
      color: #096dd9;
      border-color: #096dd9;
    }

    svg {
      stroke: currentColor;
    }
  }

  /* Reset button styling */
  .reset-button {
    background: #fafafa;
    font-size: 10px;

    &:hover {
      background: #f5f5f5;
    }
  }
`;

interface ZoomPanWrapperProps {
  children: ReactNode;
}

export function ZoomPanWrapper({ children }: ZoomPanWrapperProps) {
  return (
    <ZoomPanContainer>
      <TransformWrapper
        initialScale={1}
        initialPositionX={0}
        initialPositionY={0}
        minScale={0.5}
        maxScale={4}
        wheel={{ step: 0.1 }}
        pinch={{ step: 5 }}
        panning={{ velocityDisabled: false }}
        doubleClick={{ disabled: false, step: 0.5 }}
      >
        {({ zoomIn, zoomOut, resetTransform, ...rest }) => (
          <>
            <div className="zoom-controls">
              <button onClick={() => zoomIn()} title="Zoom In">
                +
              </button>
              <button onClick={() => zoomOut()} title="Zoom Out">
                −
              </button>
              <button
                className="reset-button"
                onClick={() => resetTransform()}
                title="Reset"
              >
                ⟲
              </button>
            </div>
            <TransformComponent>{children}</TransformComponent>
          </>
        )}
      </TransformWrapper>
    </ZoomPanContainer>
  );
}
