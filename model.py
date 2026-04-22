import tensorflow as tf
from tensorflow.keras.models import Sequential, Model
from tensorflow.keras.layers import Input, LSTM, Dense, Lambda
import tensorflow.keras.backend as K

def build_cadence_model(input_shape=('10', 3)):
    
    encoder = Sequential([
        LSTM(64, input_shape=input_shape, return_sequences=False),
        Dense(128, activation='relu'),
    ], name="Keystroke_Encoder")

    input_a = Input(shape=input_shape, name="Sample_A")
    input_b = Input(shape=input_shape, name="Sample_B")

    encoded_a = encoder(input_a)
    encoded_b = encoder(input_b)


    distance = Lambda(
        lambda x: K.exp(-K.sum(K.abs(x[0] - x[1]), axis=1, keepdims=True))
    )([encoded_a, encoded_b])

    siamese_net = Model(inputs=[input_a, input_b], outputs=distance)
    return siamese_net