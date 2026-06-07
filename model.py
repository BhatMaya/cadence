import tensorflow as tf
from tensorflow.keras.models import Model
from tensorflow.keras.layers import (
    Add,
    Dense,
    Input,
    LayerNormalization,
    Dropout,
    LSTM,
    MultiHeadAttention,
    Masking,
    Lambda
)

def transformer_encoder_block(inputs, head_size, num_heads, ff_dim, dropout=0.1):
    attention_output = MultiHeadAttention(
        num_heads=num_heads, key_dim=head_size, dropout=dropout
    )(inputs, inputs)
    x = Add()([inputs, attention_output])
    x = LayerNormalization(epsilon=1e-6)(x)
    ffn_output = Dense(ff_dim, activation="gelu")(x)
    ffn_output = Dropout(dropout)(ffn_output)
    ffn_output = Dense(inputs.shape[-1])(ffn_output)

    x = Add()([x, ffn_output])
    x = LayerNormalization(epsilon=1e-6)(x)
    return x


def build_cadence_model(input_shape=(None, 4)):
    """
    Advanced Siamese Network featuring a High-Capacity Feature Extractor.
    Utilizes an L1-normalized distance embedding layer to directly output 
    contrastive similarity metrics.
    """
    inputs = Input(shape=input_shape)
    x = Masking(mask_value=0.0)(inputs)
    x = transformer_encoder_block(x, head_size=64, num_heads=4, ff_dim=128, dropout=0.1)
    x = LSTM(64, return_sequences=False, dropout=0.1)(x)
    x = Dense(96, activation='gelu')(x)
    embedding = Dropout(0.05)(x)

    encoder = Model(inputs, embedding, name="HighCapacity_Encoder")
    input_a = Input(shape=input_shape, name="Sample_A")
    input_b = Input(shape=input_shape, name="Sample_B")

    encoded_a = encoder(input_a)
    encoded_b = encoder(input_b)

    manhattan_dist = Lambda(lambda tensors: tf.abs(tensors[0] - tensors[1]))([encoded_a, encoded_b])
    distance_sum = Lambda(lambda x: tf.reduce_sum(x, axis=1, keepdims=True))(manhattan_dist)
    output_similarity = Lambda(lambda x: tf.keras.activations.sigmoid(4.0 - x))(distance_sum)

    siamese_net = Model(inputs=[input_a, input_b], outputs=output_similarity, name="Cadence_Siamese_Net")
    return siamese_net
