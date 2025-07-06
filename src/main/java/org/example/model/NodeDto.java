package org.example.model;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

@Data
@AllArgsConstructor
@NoArgsConstructor
public class NodeDto implements Cloneable {

    private String id;
    private String name;
    private String message;

    @Override
    public NodeDto clone() {
        try {
            return (NodeDto) super.clone(); // shallow copy
        } catch (CloneNotSupportedException e) {
            throw new AssertionError(); // should never happen since we implement Cloneable
        }
    }
}
