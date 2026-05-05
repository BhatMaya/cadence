import tensorflow as tf
from tensorflow.keras.models import Model
from tensorflow.keras.layers import (
    Add,
    Dense,
    Input,
    LayerNormalization,
    LSTM,
    MultiHeadAttention,
)


@tf.keras.utils.register_keras_serializable(package="Cadence")
class ExponentialManhattanSimilarity(tf.keras.layers.Layer):
    def call(self, inputs):
        encoded_a, encoded_b = inputs
        distance = tf.reduce_sum(tf.abs(encoded_a - encoded_b), axis=1, keepdims=True)
        return tf.exp(-distance)

    def compute_output_shape(self, input_shape):
        return (input_shape[0][0], 1)


def transformer_encoder_block(inputs, head_size, num_heads, ff_dim, dropout=0.1):
    attention_output = MultiHeadAttention(
        num_heads=num_heads, key_dim=head_size
    )(inputs, inputs)
    x = Add()([inputs, attention_output])
    x = LayerNormalization(epsilon=1e-6)(x)

    ffn_output = Dense(ff_dim, activation="relu")(x)
    ffn_output = Dense(inputs.shape[-1])(ffn_output)

    x = Add()([x, ffn_output])
    x = LayerNormalization(epsilon=1e-6)(x)
    return x


def build_cadence_model(input_shape=(None, 3)):
    inputs = Input(shape=input_shape)

    x = tf.keras.layers.Masking(mask_value=0.0)(inputs)

    x = transformer_encoder_block(x, head_size=64, num_heads=4, ff_dim=128)

    x = LSTM(64, return_sequences=False)(x)

    embedding = Dense(128, activation='relu')(x)

    encoder = Model(inputs, embedding, name="Transformer_LSTM_Encoder")

    input_a = Input(shape=input_shape, name="Sample_A")
    input_b = Input(shape=input_shape, name="Sample_B")

    encoded_a = encoder(input_a)
    encoded_b = encoder(input_b)

    distance = ExponentialManhattanSimilarity(
        name="exponential_manhattan_similarity"
    )([encoded_a, encoded_b])

    siamese_net = Model(inputs=[input_a, input_b], outputs=distance)
    return siamese_net
