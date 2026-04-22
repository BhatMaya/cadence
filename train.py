import pandas as pd
from model import build_siamese_model
from util import create_pairs         

# 1. Load features from Aryaman
data = pd.read_csv('processed_keystrokes.csv')

# 2. Preparing pairs
left_X, right_X, labels = create_pairs(data)

# 3. Initialization
model = build_siamese_model(input_shape=('number of inputs', 3))

# 4. Training
model.fit([left_X, right_X], labels, epochs=50, batch_size=32)

# 5. Export
model.save('cadence_base_model.h5')