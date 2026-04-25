#!/bin/bash
set -e

# Install frontend dependencies
cd frontend
npm install --legacy-peer-deps
cd ..

# Install backend dependencies
cd backend
pip install -r requirements.txt -q
cd ..
